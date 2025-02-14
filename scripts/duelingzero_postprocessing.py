#!/usr/bin/env python3
# Postprocessing script for adding the toolhead / gantry moves to avoid collision for D0. 
# Base on duel.py from zruncho3d ( https://github.com/zruncho3d/DuelingZero/blob/main/src/duel.py )
# Script to run a Dueling Zero printer with two toolheads.
#
# To see arguments, invoke this script with:
#   ./dueling_postprocessing.py -h
#
# Sample invocations:
#   ./dueling_postprocessing.py --input sample.gcode --output sample_d0_ready.gcode
#   ./dueling_postprocessing.py --verbose --input sample.gcode --output sample_d0_ready.gcode
#       for more output on the console
#   ./dueling_postprocessing.py --verboseGcode  sliced.gcode
#       for commented gcode for slicer (i.e. post-processing call)
# Features:
#  - Collision avoidance based on zruncho3d code.
#  - Split extrusion move
#  - Feed rate restoration
#  - Interface usable by Slicers
#  - Z-lift for inserted moves
#  - Optional comments in output gcode to mark inserted sections
#  - Once processed output can be reprocessed with no change, means no collision causing gcode insertions ;)

import argparse
import os
import sys

from gcodeparser import GcodeParser, GcodeLine
from gcodeparser.commands import Commands

from toolhead import check_for_overlap, check_for_overlap_sweep
from toolhead import Y_HEIGHT, T0_X_BACKOFF, T1_X_BACKOFF, Y_HIGH, Y_LOW
from toolhead import X_BACKOFF_LEN, BACKOFF_SPEED, PARK_SPEED, SHUFFLE_SPEED,MOVE_TO_SPEED, TOOLHEAD_Y_HEIGHT
from toolhead import LEFT_PARK_POS, RIGHT_PARK_POS
from toolhead import Z_LIFT
from point import Point

T0: GcodeLine = GcodeLine(('T', 0), {}, "")
T1: GcodeLine = GcodeLine(('T', 1), {}, "")

PP_comment : str = "PPfD0"   # Post-processed for Dueling Zero

class DuelRunner:
    def __init__(self, passed_args):
        """Init function for DuelRunner. Storing passed arguments and initialising statistics"""
        if passed_args is not None:
            self.output = None  # output file handler
            self.verbose: bool   = passed_args.verbose
            self.verboseGcode: bool= passed_args.verboseGcode
        else:
            self.output = None  # output file handler
            self.verbose: bool = True
            self.verboseGcode: bool = True
        self.z_lifted: bool = False
        self.last_feed_rate:float = 0
        self.need_to_restore_feed_rate: bool = False
        # Initialize metrics
        self.simple_shuffles_t0:int = 0
        self.simple_shuffles_t1:int = 0
        self.backup_shuffles_t0:int = 0
        self.backup_shuffles_t1:int = 0
        self.segmented_shuffles_t0:int = 0
        self.segmented_shuffles_t1:int = 0
        self.park_moves_t0 : int = 0
        self.park_moves_t1 : int = 0

    def t0_park(self)-> Point:
        """Park TO at LEFT_PARK_POS. Activates toolhead T0"""
        self.park_moves_t0 += 1
        self.z_up()
        for gcode in ["T0 ; %s t0_park"%PP_comment, "G0 X%s F%s" % (LEFT_PARK_POS.x, PARK_SPEED), "G0 Y%s F%s" % (LEFT_PARK_POS.y, PARK_SPEED)]:
            self.write_gcode_to_file(gcode)
        self.z_down()
        self.need_to_restore_feed_rate = True
        return LEFT_PARK_POS

    def t1_park(self) -> Point:
        """Park T1 at RIGHT_PARK_POS. Activates toolhead T1"""
        self.park_moves_t1 += 1
        self.z_up()
        for gcode in ["T1 ; %s t1_park"%PP_comment, "G0 X%s F%s" % (RIGHT_PARK_POS.x, PARK_SPEED), "G0 Y%s F%s" % (RIGHT_PARK_POS.y, PARK_SPEED)]:
            self.write_gcode_to_file(gcode)
        self.z_down()
        self.need_to_restore_feed_rate = True
        return RIGHT_PARK_POS

    def t0_backoff(self, pos : Point) ->Point:
        """Backoff T0 to clear path for T1. NO activation of T0"""
        for gcode in ["; %s t0_backoff" % PP_comment, "G0 X%s F%s" % (T0_X_BACKOFF, BACKOFF_SPEED)]:
            self.write_gcode_to_file(gcode)
        self.need_to_restore_feed_rate = True
        return Point(T0_X_BACKOFF, pos.y)
    def t1_backoff(self, pos:Point ) -> Point:
        """Backoff T1 to clear path for T0. NO activation of T1"""
        for gcode in ["; %s t1_backoff" % PP_comment, "G0 X%s F%s" % (T1_X_BACKOFF, BACKOFF_SPEED)]:
            self.write_gcode_to_file(gcode)
        self.need_to_restore_feed_rate = True
        return Point(T1_X_BACKOFF, pos.y)

    def t0_shuffle(self, pos : Point) -> Point:
        """Shuffle T0 from Y_LOW to Y_HIGH or vice versa. Activation of T0"""
        assert pos.y == Y_HIGH or pos.y == Y_LOW
        new_y = None
        if pos.y == Y_LOW:
            new_y = Y_HIGH
        elif pos.y == Y_HIGH:
            new_y = Y_LOW
        for gcode in ["T0 ; %s t0_shuffle"%PP_comment, "G0 Y%s F%s" % (new_y, SHUFFLE_SPEED)]:
            self.write_gcode_to_file(gcode)
        self.need_to_restore_feed_rate = True
        return Point(pos.x, new_y)

    def t1_shuffle(self, pos : Point) -> Point:
        """Shuffle T1 from Y_LOW to Y_HIGH or vice versa. Activation of T1"""
        assert pos.y == Y_HIGH or pos.y == Y_LOW
        new_y = None
        if pos.y == Y_LOW:
            new_y = Y_HIGH
        elif pos.y == Y_HIGH:
            new_y = Y_LOW
        for gcode in ["T1 ; %s t1_shuffle"%PP_comment, "G0 Y%s F%s" % (new_y, SHUFFLE_SPEED)]:
            self.write_gcode_to_file(gcode)
        self.need_to_restore_feed_rate = True
        return Point(pos.x, new_y)

    def t0_go_to_w_a(self, pos : Point) -> Point:
        """Activate and move T0 to new position"""
        for gcode in ["T0 ; %s t0_go_to"%PP_comment, "G0 X%s Y%s F%s" % (pos.x, pos.y, MOVE_TO_SPEED)]:
            self.write_gcode_to_file(gcode)
        return pos
    def t0_go_to(self, pos : Point) -> Point:
        """Move T0 to new position. NO activation"""
        for gcode in ["; %s t0_go_to"%PP_comment, "G0 X%s Y%s F%s" % (pos.x, pos.y, MOVE_TO_SPEED)]:
            self.write_gcode_to_file(gcode)
        self.need_to_restore_feed_rate = True
        return pos

    def t1_go_to_w_a(self, pos : Point) -> Point:
        """Activate and move T1 to new position"""
        for gcode in ["T1 ; %s t1_go_to"%PP_comment, "G0 X%s Y%s F%s" % (pos.x, pos.y, MOVE_TO_SPEED)]:
            self.write_gcode_to_file(gcode)
        self.need_to_restore_feed_rate = True
        return pos
    def t1_go_to(self, pos : Point) -> Point:
        """Move T1 to new position. NO activation"""
        for gcode in ["; %s t1_go_to"%PP_comment, "G0 X%s Y%s F%s" % (pos.x, pos.y, MOVE_TO_SPEED)]:
            self.write_gcode_to_file(gcode)
        self.need_to_restore_feed_rate = True
        return pos

    def t0_activate(self, pos : Point ) -> Point:
        """Activate T0. Nothing else"""
        for gcode in ["T0 ; %s t0_activate"%PP_comment]:
            self.write_gcode_to_file(gcode)
        return pos

    def t1_activate(self, pos:Point) -> Point:
        """Activate T1. Nothing else"""
        for gcode in ["T1 ; %s t1_activate"%PP_comment]:
            self.write_gcode_to_file(gcode)
        return pos

    def z_up(self):
        """Lifts Z if Z_LIFT > 0 and current state is not lifted already."""
        if Z_LIFT > 0 and self.z_lifted == False:
            self.z_lifted = True
            for gcode in ["G91", "G0 Z%s" % Z_LIFT , "G90"]:
                self.write_gcode_to_file(gcode)

    def z_down(self):
        """Lower Z if Z_LIFT > 0 and current state is lifted."""
        if Z_LIFT > 0 and self.z_lifted == True:
            self.z_lifted = False
            for gcode in ["G91", "G0 Z-%s" % Z_LIFT , "G90"]:
                self.write_gcode_to_file(gcode)

    def restore_feed_rate(self):
        """Restore feed rate if required"""
        if self.need_to_restore_feed_rate:
            if self.last_feed_rate > 0:
                for gcode in ["G1 F%s ; restored feed_rate by %s" % (self.last_feed_rate , PP_comment)]:
                    self.write_gcode_to_file(gcode)
            self.need_to_restore_feed_rate = False

    def write_gcode_to_file(self, gcode_line: str):
        """Write given string to output file. with stripped double spaces"""
        if self.output:
            # self.output.write(gcode_line + "\n")  #  may include double spaces
            self.output.write(' '.join(gcode_line.split()) + "\n")   # strips double spaces

    @staticmethod
    def get_corresponding_x(toolhead_pos: Point, next_toolhead_pos: Point, target_y: float) -> float:
        """Get the corresponding x for a target y; useful for splitting moves for segmented avoidance."""
        # Handle vertical case.
        if toolhead_pos.x == next_toolhead_pos.x:
            return toolhead_pos.x

        # Compute the new path portion that gets us clear of the future parked inactive extruder in the Y.
        # y = mx + b; slope (m); intercept (b);
        m = (next_toolhead_pos.y - toolhead_pos.y) / (next_toolhead_pos.x - toolhead_pos.x)
        b = toolhead_pos.y - m * toolhead_pos.x
        # Find point on the line where Y-val = min to clear.
        # x = (<Y val> - b) / m
        x = (target_y - b) / m
        return x

    def do_partial_org_move_start(self, start_pos : Point, mid_pos : Point, final_pos: Point, line: GcodeLine) -> Point:
        """Execute the first part of movement vom start_pos to final_pos. By moving from start_pos up to mid_pos. Extrusion and feed rate are extracted from original line"""
        cmd:str = line.command[0] + "%d" % line.command[1]
        fraction_of_move:float = (mid_pos.y - start_pos.y) / (final_pos.y - start_pos.y)
        if line.get_param('X') is not None:
            cmd += " X%f" % mid_pos.x
        if line.get_param('Y') is not None:
            cmd += " Y%f"% mid_pos.y
        if line.get_param('E') is not None:
            cmd += " E%f" %(line.get_param('E') * fraction_of_move)
        if line.get_param('F') is not None:
            cmd += " F%f" %(line.get_param('F'))
        # build  partial original command
        self.write_gcode_to_file(cmd)
        return mid_pos

    def do_partial_org_move_end(self, start_pos : Point, mid_pos : Point, final_pos: Point, line: GcodeLine) -> Point:
        """Execute the second part of movement from start_pos to final_pos by moving from mid_pos up to final_pos. Extrusion and feed rate are extracted from original line"""
        cmd:str = line.command[0] + "%d" % line.command[1]
        fraction_of_move = (mid_pos.y - start_pos.y) / (final_pos.y - start_pos.y)
        if line.get_param('X') is not None:
            cmd += " X%f" % final_pos.x
        if line.get_param('Y') is not None:
            cmd += " Y%f"% final_pos.y
        if line.get_param('E') is not None:
            cmd += " E%f" %(line.get_param('E') * (1-fraction_of_move))
        if line.get_param('F') is not None:
            cmd += " F%f" %(line.get_param('F'))
        # build  partial original command
        self.write_gcode_to_file(cmd)
        return final_pos

    def do_right_segmented_sequence(self, toolhead_pos : Point, target_y : float, next_toolhead_pos: Point, inactive_toolhead_pos: Point, line:GcodeLine):
        # If a simple backup-X move, followed by resume-X, were to cause a collision,
        # then execute enough of the move to clear the shuffled inactive extruder, back it away,
        # do the shuffle, then resume with the second part of the move.

        x = self.get_corresponding_x(toolhead_pos, next_toolhead_pos, target_y)
        mid_pos = Point(x, target_y)
        if self.verboseGcode: self.write_gcode_to_file("; Right segmented sequence start")
        if self.verbose:print("  ! Right segmented sequence")
        if self.verbose:print("  ! Doing first part of move sequence till mid_pos")
        self.do_partial_org_move_start (toolhead_pos, mid_pos, next_toolhead_pos, line)
        if self.verbose:print("  ! Backing up t0")
        self.z_up()
        self.t0_backoff(Point(0,0)) # no activation needed
        if self.verbose:print("  ! Shuffling inactive t1")
        right_toolhead_pos = self.t1_shuffle(inactive_toolhead_pos)
        if self.verbose:print(" ! Restoring t0 to mid_pos after backup: %s" % mid_pos)
        self.t0_go_to_w_a(mid_pos)
        self.z_down()
        if self.verbose:print(" ! Doing second part of move sequence from %s to %s" % (mid_pos,next_toolhead_pos ))
        self.restore_feed_rate()
        self.do_partial_org_move_end(toolhead_pos, mid_pos, next_toolhead_pos, line)
        if self.verboseGcode:self.write_gcode_to_file("; Right segmented sequence end")
        return right_toolhead_pos

    def do_right_backup_sequence(self, toolhead_pos, inactive_toolhead_pos, line):
        if self.verboseGcode :self.write_gcode_to_file("; Right Backup sequence start")
        if self.verbose : print(" ! Right backup sequence")
        if self.verbose : print(" ! Backup sequence: t0 Backing up")
        self.z_up()
        self.t0_backoff(Point(0,0))  # no T0 needed
        if self.verbose : print(" ! Shuffling inactive t1")
        right_toolhead_pos = self.t1_shuffle(inactive_toolhead_pos)
        # Restore original x for active instance
        if self.verbose : print(" ! Resuming after backup: t0 restoring to %s" % toolhead_pos)
        self.t0_go_to_w_a(toolhead_pos)
        self.z_down()
        if self.verboseGcode: self.write_gcode_to_file("; Right backup sequence end")
        if self.verbose : print(" ! Running original move.")
        self.restore_feed_rate()
        self.write_gcode_to_file(line.gcode_str)
        return right_toolhead_pos

    def do_left_segmented_sequence(self, toolhead_pos, target_y, next_toolhead_pos, inactive_toolhead_pos, line: GcodeLine):
        # If a simple backup-X move, followed by resume-X, were to cause a collision,
        # then execute enough of the move to clear the shuffled inactive extruder, back it away,
        # do the shuffle, then resume with the second part of the move.
        x = self.get_corresponding_x(toolhead_pos, next_toolhead_pos, target_y)
        mid_pos = Point(x, target_y)
        if self.verboseGcode : self.write_gcode_to_file("; Left segmented sequence start")
        if self.verbose : print(" ! Left segmented sequence")
        if self.verbose : print(" ! Doing first part of move sequence")
        self.do_partial_org_move_start (toolhead_pos, mid_pos, next_toolhead_pos, line)
        if self.verbose : print(" ! Backing up t1")
        self.z_up()
        self.t1_backoff(Point(0,0)) # no activation needed
        if self.verbose : print(" ! Shuffling inactive 0")
        left_toolhead_pos = self.t0_shuffle(inactive_toolhead_pos)
        if self.verbose : print(" ! Restoring t1 to mid_pos after backup: %s" % mid_pos)
        self.t1_go_to_w_a(mid_pos)
        self.z_down()
        self.restore_feed_rate()
        if self.verbose : print(" ! Doing second part of move sequence from %s to %s"  % (mid_pos,next_toolhead_pos ))
        self.do_partial_org_move_end(toolhead_pos, mid_pos, next_toolhead_pos, line)
        if self.verboseGcode: self.write_gcode_to_file("; Left segmented sequence end")
        return left_toolhead_pos

    def do_left_backup_sequence(self, toolhead_pos, inactive_toolhead_pos, line):
        self.write_gcode_to_file("; Left backup sequence start")
        print("  ! Left backup sequence ")
        print("  ! Backup shuffle: t1 Backing up")
        self.z_up()
        self.t1_backoff(Point(0,0)) # no activation needed
        print("  ! Shuffling inactive t0")
        left_toolhead_pos = self.t0_shuffle(inactive_toolhead_pos)
        # Restore original x for active instance
        print("  ! Resuming after backup: t1 restoring to %s" % toolhead_pos)
        self.t1_go_to_w_a(toolhead_pos)
        self.z_down()
        self.restore_feed_rate()
        self.write_gcode_to_file("; Left backup sequence end")
        print("  ! Running original move.")

        self.write_gcode_to_file(line.gcode_str)
        return left_toolhead_pos

    def do_right_simple_shuffle(self,toolhead_pos: Point, inactive_toolhead_pos: Point, line: GcodeLine) -> Point :
        self.simple_shuffles_t1 += 1
        if self.verboseGcode: self.write_gcode_to_file("; Right simple shuffle start")
        if self.verbose: print(" ! Right simple shuffle")
        if self.verbose: print(" ! Shuffling inactive t1")
        self.z_up()
        right_toolhead_pos = self.t1_shuffle(inactive_toolhead_pos)
        self.z_down()
        self.t0_activate(toolhead_pos)
        self.restore_feed_rate()
        if self.verboseGcode: self.write_gcode_to_file("; Right simple shuffle end")
        self.write_gcode_to_file(line.gcode_str)
        return right_toolhead_pos

    def do_left_simple_shuffle(self,toolhead_pos: Point, inactive_toolhead_pos: Point, line: GcodeLine) -> Point :
        self.simple_shuffles_t0 += 1
        if self.verboseGcode: self.write_gcode_to_file("; Left simple shuffle start")
        if self.verbose: print(" ! Left simple shuffle")
        if self.verbose: print(" ! Shuffling inactive t0")
        left_toolhead_pos = self.t0_shuffle(inactive_toolhead_pos)
        self.t1_activate(toolhead_pos)
        self.restore_feed_rate()
        if self.verboseGcode: self.write_gcode_to_file("; Left simple shuffle end")
        self.write_gcode_to_file(line.gcode_str)
        return left_toolhead_pos

    def play_gcodes_file(self, gcode_file:str):
        """Post processes the given file, overwriting the original input as requested by i.e. Orca slicer"""
        with open(gcode_file, 'r') as f:
            file_content = f.read()
        f.close()
        # now open the same file as output.
        self.output = open(gcode_file, "w")
        if self.output is None:
            print("Could not create output file: %s" % gcode_file)
            sys.exit(1)
        self.play_gcodes(file_content)

    def play_gcodes_file_sep(self, f_input:str, f_output:str):
        """Post processes the given input file, overwriting the given output file. Useful for "chained" call testing and inspecting the inserted gcodes"""
        with open(f_input, 'r') as f:
            file_content = f.read()
        f.close()
        # now open output. which could be the same as input
        self.output = open(f_output, "w")  # reset given output file
        if self.output is None:
            print("Could not create output file: %s" % f_output)
            sys.exit(1)

        self.play_gcodes(file_content)

    def play_gcodes(self, input_file_content):
        """Execute all G-codes from file content, inserting backups/shuffles/splits as needed."""
        lines = GcodeParser(input_file_content, include_comments=True).lines

        right_toolhead_pos = RIGHT_PARK_POS
        left_toolhead_pos = LEFT_PARK_POS
        active_instance: str = 'left'

        for line in lines:
            if self.verbose :print("pos T0: X:%.1f Y:%.1f" % (left_toolhead_pos.x, left_toolhead_pos.y))
            if self.verbose :print("pos T1: X:%.1f Y:%.1f" % (right_toolhead_pos.x, right_toolhead_pos.y))
            if self.verbose :print("input : " + line.gcode_str)


            if line.type == Commands.TOOLCHANGE:
                # Decide on action     
                if line.command == T0.command:
                    if active_instance == 'left':
                        if self.verbose :print( "Tool already active")
                    else:
                        if PP_comment in line.comment :
                            # Just swap active instance without adding additional gcode
                            if self.verbose :print("Tool activation was inserted by PostProcessing.")
                            active_instance = 'left'
                        else:
                            right_toolhead_pos = self.t1_park()
                            self.restore_feed_rate()
                            active_instance = 'left'
                elif line.command == T1.command:
                    if active_instance == 'right':
                        if self.verbose :print("Tool already active")
                    else:
                        if PP_comment in line.comment :
                            # Just swap active instance without adding additional gcode
                            if self.verbose :print("Tool activation was inserted by PostProcessing.")
                            active_instance = 'right'
                        else:
                            if self.verbose :print("Park T0")
                            left_toolhead_pos = self.t0_park()
                            self.restore_feed_rate()
                            active_instance = 'right'
                else:
                    print("Unknown toolhead number")
                    sys.exit(1)

                # Add input toolchange to file, so the printer knows about it, too
                if PP_comment in line.comment:
                    self.write_gcode_to_file(line.gcode_str)
                else:
                    # Add comment, that this Toolchange has been post processed i.e. parking inserted
                    self.write_gcode_to_file(line.gcode_str + " ; handled by %s " % PP_comment)

            elif line.type == Commands.MOVE:

                # Form target of move.
                next_toolhead_pos : Point = Point(0,0)
                inactive_toolhead_pos : Point = Point(0,0)
                if active_instance == 'left':
                    next_toolhead_pos = left_toolhead_pos.copy()
                    toolhead_pos = left_toolhead_pos.copy()
                    inactive_toolhead_pos = right_toolhead_pos.copy()
                elif active_instance == 'right':
                    next_toolhead_pos = right_toolhead_pos.copy()
                    toolhead_pos = right_toolhead_pos.copy()
                    inactive_toolhead_pos = left_toolhead_pos.copy()
                else:
                    print ("No active_instance set!")
                    sys.exit(1)

                # update new pos
                if line.get_param('X') is not None:
                    next_toolhead_pos.x = float(line.get_param('X'))
                if line.get_param('Y') is not None:
                    next_toolhead_pos.y = float(line.get_param('Y'))

                # extract more parameter from the move like and F
                if line.get_param('F') is not None:
                    if self.need_to_restore_feed_rate:
                        print("Error: Feed rate was not restored before!")
                        sys.exit(1)
                    else:
                        self.last_feed_rate =line.get_param('F')
                        self.need_to_restore_feed_rate = False  # we just read one. no need to set it, if not changed

                # Ensure move is safe.
                # (1) Check against destination bounding box.
                overlap_rect = check_for_overlap(inactive_toolhead_pos, next_toolhead_pos)
                if overlap_rect:
                    if self.verbose :print("overlap end pos: inactive: %s ,next pos: %s" %(inactive_toolhead_pos, next_toolhead_pos))

                # (2) Check swept area against inactive bounding box
                overlap_swept = check_for_overlap_sweep(toolhead_pos, next_toolhead_pos, inactive_toolhead_pos)
                if overlap_swept:
                    if self.verbose :print("overlap swept  inactive: %s ,current pos : %s, next pos: %s" %(inactive_toolhead_pos,toolhead_pos, next_toolhead_pos))

                # Check if a single move will suffice.
                if overlap_rect or overlap_swept:
                    min_y_to_clear_inactive_toolhead = TOOLHEAD_Y_HEIGHT
                    max_y_to_clear_inactive_toolhead = Y_HEIGHT - min_y_to_clear_inactive_toolhead

                    if active_instance == 'left':
                        # Target must be on the right.

                        # Simple shuffle if we're not in the end zone yet.
                        if left_toolhead_pos.x < T0_X_BACKOFF:
                            right_toolhead_pos = self.do_right_simple_shuffle(left_toolhead_pos, right_toolhead_pos, line)

                        # Segmented move: active is now in the front, and would conflict with a back-to-front shuffled inactive toolhead
                        elif toolhead_pos.y <= min_y_to_clear_inactive_toolhead:
                            self.segmented_shuffles_t1 += 1
                            right_toolhead_pos = self.do_right_segmented_sequence(left_toolhead_pos, min_y_to_clear_inactive_toolhead,
                                                                                  next_toolhead_pos, right_toolhead_pos, line)

                        # Segmented move: active is in the rear
                        elif toolhead_pos.y >= max_y_to_clear_inactive_toolhead:
                            self.segmented_shuffles_t1 += 1
                            right_toolhead_pos = self.do_right_segmented_sequence(left_toolhead_pos, max_y_to_clear_inactive_toolhead,
                                                                                  next_toolhead_pos, right_toolhead_pos, line)

                        # Backup move.
                        else:
                            self.backup_shuffles_t1 += 1
                            right_toolhead_pos = self.do_right_backup_sequence(left_toolhead_pos, right_toolhead_pos, line)

                    elif active_instance == 'right':
                        # Target must be on the left.

                        # Simple shuffle if we're not in the end zone yet.
                        if toolhead_pos.x > X_BACKOFF_LEN:
                            left_toolhead_pos = self.do_left_simple_shuffle(right_toolhead_pos, left_toolhead_pos, line)


                        # Segmented move: active is in the front
                        elif toolhead_pos.y <= min_y_to_clear_inactive_toolhead:
                            self.segmented_shuffles_t0 += 1
                            left_toolhead_pos = self.do_left_segmented_sequence(right_toolhead_pos, min_y_to_clear_inactive_toolhead,
                                                                                  next_toolhead_pos, left_toolhead_pos, line)

                        # Segmented move: active is in the rear
                        elif toolhead_pos.y >= max_y_to_clear_inactive_toolhead:
                            self.segmented_shuffles_t0 += 1
                            left_toolhead_pos = self.do_left_segmented_sequence(right_toolhead_pos, max_y_to_clear_inactive_toolhead,
                                                                                  next_toolhead_pos, left_toolhead_pos, line)

                        # Backup move.
                        else:
                            self.backup_shuffles_t0 += 1
                            left_toolhead_pos = self.do_left_backup_sequence(right_toolhead_pos, left_toolhead_pos, line)

                # If no overlap, just do the straight line.
                else:
                    self.write_gcode_to_file(line.gcode_str)

                # Update position of toolhead after execution
                if active_instance == 'left':
                    left_toolhead_pos = next_toolhead_pos
                elif active_instance == 'right':
                    right_toolhead_pos = next_toolhead_pos
            else:
                # Add all other lines, non toolchange / non move lines to file
                self.write_gcode_to_file(line.gcode_str)

    def run(self):
        if args.gcodefile and not os.path.exists(args.gcodefile):
            print("Invalid input file path: %s" % args.gcodefile)
            sys.exit(1)
        if args.input and not os.path.exists(args.input):
            print("Invalid input file path: %s" % args.input)
            sys.exit(1)

        print("Running:")
        if args.gcodefile:
            self.play_gcodes_file(args.gcodefile)
        elif args.input and args.output:
            self.play_gcodes_file_sep(args.input, args.output)

        if self.output:  # file open was successful, then close
            self.output.close()
        print("Finished.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post process a gcode file for use with a dual gantry printer.")
    parser.add_argument('--input', help="Input gcode filepath")
    parser.add_argument('--output', help="Output gcode filepath")
    parser.add_argument('--verbose', help="Use more-verbose debug output", action='store_true')
    parser.add_argument('--verboseGcode', help="Use more comments in output gcode", action='store_true')
    parser.add_argument('gcodefile', nargs='?')

    args = parser.parse_args()

    dr = DuelRunner(args)
    dr.run()