#!/usr/bin/env python3
# Postprocessing script for adding the toolhead / gantry moves to avoid collision for D0. 
# Base on duel.py from zruncho3d ( https://github.com/zruncho3d/DuelingZero/blob/main/src/duel.py )
# Script to run a Dueling Zero printer with two toolheads.
#
# To see arguments, invoke this script with:
#   ./dueling_postprocessing.py -h
#
# Sample invocations:
#   ./dueling_postprocessing.py --home --home_after  sample.gcode --output sample_d0_ready.gcode
#
# DONE Repeated parking which is not required, but reprocessing a file again  should not alter the output (i -> o -> o)
# FIXME Initial check of active toolhead is working. But added parking routine from the  first run changes the active tool head!
#  maybe used T0 / T1 with comment for parking / (by this script in general added tool changes) added moves? ?
#  should be fine for now for single execution of script.
# FIXME Z lift
# FIXME Extrusion in split print moves

import argparse
import os
import sys

from gcodeparser import GcodeParser, GcodeLine
from mako.runtime import Namespace
from sympy import false

from toolhead import check_for_overlap, check_for_overlap_sweep
from toolhead import Y_HEIGHT, T0_X_BACKOFF, T1_X_BACKOFF, Y_HIGH, Y_LOW, X_HIGH, X_LOW
from toolhead import X_BACKOFF_LEN, BACKOFF_SPEED, PARK_SPEED, SHUFFLE_SPEED,MOVE_TO_SPEED, TOOLHEAD_Y_HEIGHT
from point import Point

T0 = GcodeLine(('T', 0), {}, "")
T1 = GcodeLine(('T', 1), {}, "")

PP_comment = "PPfD0"   # Post processed for Dueling Zero

class DuelRunner:

    def __init__(self, passed_args: Namespace):
        if passed_args is not None:
            self.args: Namespace = passed_args
            self.output = None
            self.output_filename : str = passed_args.output
            self.input: str = passed_args.input

            if passed_args.gcodefile != "":
                self.input = passed_args.gcodefile
                self.output_filename = passed_args.gcodefile

        # Initialize metrics
        self.simple_shuffles:int = 0
        self.backup_shuffles:int = 0
        self.segmented_shuffles:int = 0



    def home(self):
        # Make sure your homing works correctly for homing xy only.
        # Change if your homing requires a different active tool head.
        for gcode_str in ["T0 ; %s home"%PP_comment, "G28 X Y"]:
            self.write_gcode_to_file(gcode_str)

    def t0_park(self)-> Point:
        for gcode in ["T0 ; %s t0_park"%PP_comment, "G0 X%s F%s" % (X_LOW, PARK_SPEED), "G0 Y%s F%s" % (Y_HIGH, PARK_SPEED)]:
            self.write_gcode_to_file(gcode)
        return Point(X_LOW, Y_HIGH)

    def t1_park(self) -> Point:
        for gcode in ["T1 ; %s t1_park"%PP_comment, "G0 X%s F%s" % (X_HIGH, PARK_SPEED), "G0 Y%s F%s" % (Y_HIGH, PARK_SPEED)]:
            self.write_gcode_to_file(gcode)
        return Point(X_HIGH, Y_HIGH)

    def t0_backoff(self, pos:Point) ->Point:
        # TODO create second version without activation
        #for gcode in ["T0 ; %s t0_backoff"%PP_comment, "G0 X%s F%s" % (T0_X_BACKOFF, BACKOFF_SPEED)]:
        for gcode in ["; %s t0_backoff" % PP_comment, "G0 X%s F%s" % (T0_X_BACKOFF, BACKOFF_SPEED)]:
            self.write_gcode_to_file(gcode)
        return Point(T0_X_BACKOFF, pos.y)
    def t1_backoff(self, pos:Point ) -> Point:
        # TODO create second version without activation
        #for gcode in ["T1 ; %s t1_backoff"%PP_comment, "G0 X%s F%s" % (T1_X_BACKOFF, BACKOFF_SPEED)]:
        for gcode in ["; %s t1_backoff" % PP_comment, "G0 X%s F%s" % (T1_X_BACKOFF, BACKOFF_SPEED)]:
            self.write_gcode_to_file(gcode)
        return Point(T1_X_BACKOFF, pos.y)

    def t0_shuffle(self, pos : Point) -> Point:
        assert pos.y == Y_HIGH or pos.y == Y_LOW
        new_y = None
        if pos.y == Y_LOW:
            new_y = Y_HIGH
        elif pos.y == Y_HIGH:
            new_y = Y_LOW
        for gcode in ["T0 ; %s t0_shuffle"%PP_comment, "G0 Y%s F%s" % (new_y, SHUFFLE_SPEED)]:
            self.write_gcode_to_file(gcode)
        return Point(pos.x, new_y)

    def t1_shuffle(self, pos : Point) -> Point:
        assert pos.y == Y_HIGH or pos.y == Y_LOW
        new_y = None
        if pos.y == Y_LOW:
            new_y = Y_HIGH
        elif pos.y == Y_HIGH:
            new_y = Y_LOW
        for gcode in ["T1 ; %s t1_shuffle"%PP_comment, "G0 Y%s F%s" % (new_y, SHUFFLE_SPEED)]:
            self.write_gcode_to_file(gcode)
        return Point(pos.x, new_y)

    def t0_go_to_w_a(self, pos : Point) -> Point:
        for gcode in ["T0 ; %s t0_go_to"%PP_comment, "G0 X%s Y%s F%s" % (pos.x, pos.y, MOVE_TO_SPEED)]:
            self.write_gcode_to_file(gcode)
        return pos
    def t0_go_to(self, pos : Point) -> Point:
        for gcode in ["; %s t0_go_to"%PP_comment, "G0 X%s Y%s F%s" % (pos.x, pos.y, MOVE_TO_SPEED)]:
            self.write_gcode_to_file(gcode)
        return pos

    def t1_go_to_w_a(self, pos : Point) -> Point:
        for gcode in ["T1 ; %s t1_go_to"%PP_comment, "G0 X%s Y%s F%s" % (pos.x, pos.y, MOVE_TO_SPEED)]:
            self.write_gcode_to_file(gcode)
        return pos
    def t1_go_to(self, pos : Point) -> Point:
        for gcode in ["; %s t1_go_to"%PP_comment, "G0 X%s Y%s F%s" % (pos.x, pos.y, MOVE_TO_SPEED)]:
            self.write_gcode_to_file(gcode)
        return pos

    def t0_activate(self, pos : Point ) -> Point:
        for gcode in ["T0 ; %s t0_activate"%PP_comment]:
            self.write_gcode_to_file(gcode)
        return pos

    def t1_activate(self, pos:Point) -> Point:
        for gcode in ["T1 ; %s t1_activate"%PP_comment]:
            self.write_gcode_to_file(gcode)
        return pos

    @staticmethod
    def is_toolchange_gcode(line : GcodeLine) -> bool:
        return line.command == T0.command or line.command == T1.command

    @staticmethod
    def is_move_gcode(line : GcodeLine) -> bool:
        return line.command == ('G', 0) or line.command == ('G', 1)

    def write_gcode_to_file(self, gcode_line: str):
        if self.output:
            # self.output.write(gcode_line + "\n")
            self.output.write(' '.join(gcode_line.split()) + "\n")

    @staticmethod
    def get_corresponding_x(toolhead_pos, next_toolhead_pos, target_y):
        """Get the corresponding x for a target y; useful for splitting moves for segmented avoidance."""

        # Handle vertical case.
        if toolhead_pos.x == next_toolhead_pos.x:
            return toolhead_pos.x

        # Compute the new path portion that gets us clear of the future parked inactive extruder in the Y.
        # y = mx + b; slope (m); intercept (b);
        m = (next_toolhead_pos.y - toolhead_pos.y) / (next_toolhead_pos.x - toolhead_pos.x)
        b = toolhead_pos.y - m * toolhead_pos.x  # changed from b = (toolhead_pos.y - m) / toolhead_pos.x

        # Find point on the line where Y-val = min to clear.
        # x = (<Y val> - b) / m
        x = (target_y - b) / m
        return x

    def do_right_segmented_sequence(self, toolhead_pos, target_y, next_toolhead_pos, inactive_toolhead_pos):
        # If a simple backup-X move, followed by resume-X, were to cause a collision,
        # then execute enough of the move to clear the shuffled inactive extruder, back it away,
        # do the shuffle, then resume with the second part of the move.

        # TODO: handle extrusion in the move split and retain the g-code used for the move (G0 vs G1, etc.)
        # TODO: keep feedrate for original
        x = self.get_corresponding_x(toolhead_pos, next_toolhead_pos, target_y)
        mid_pos = Point(x, target_y)
        self.write_gcode_to_file("; Right segmented sequence start")
        print("  ! Segmented sequence")
        print("  ! Doing first part of move sequence")
        # TODO  orignal print move
        self.t0_go_to(mid_pos)
        print("  ! Backing up t0")
        self.t0_backoff(Point(0,0)) # no activation needed
        print("  ! Shuffling inactive 1")
        right_toolhead_pos = self.t1_shuffle(inactive_toolhead_pos)
        print(" ! Restoring t0 to mid_pos after backup: %s" % mid_pos)
        self.t0_go_to_w_a(mid_pos)
        print(" ! Doing second part of move sequence : %s to %s" % (mid_pos,next_toolhead_pos ))
        # TODO  orignal print move
        self.t0_go_to(next_toolhead_pos)
        self.write_gcode_to_file("; Right segmented sequence end")
        return right_toolhead_pos

    def do_right_backup_sequence(self, toolhead_pos, inactive_toolhead_pos, line):
        self.write_gcode_to_file("; Right Backup sequence start")
        print("  ! Backup sequence")
        print("  ! Backup sequence: t0 Backing up")
        self.t0_backoff(Point(0,0))  # no T0 needed
        print("  ! Shuffling inactive t1")
        right_toolhead_pos = self.t1_shuffle(inactive_toolhead_pos)
        # Restore original x for active instance
        print(" ! Resuming after backup: t0 restoring to %s" % toolhead_pos)
        self.t0_go_to_w_a(toolhead_pos)
        print(" ! Running original move.")
        self.write_gcode_to_file(line.gcode_str)
        self.write_gcode_to_file("; Right backup sequence end")
        return right_toolhead_pos

    def do_left_segmented_sequence(self, toolhead_pos, target_y, next_toolhead_pos, inactive_toolhead_pos):
        # If a simple backup-X move, followed by resume-X, were to cause a collision,
        # then execute enough of the move to clear the shuffled inactive extruder, back it away,
        # do the shuffle, then resume with the second part of the move.

        # TODO: handle extrusion in the move split and retain the g-code used for the move (G0 vs G1, etc.)
        # TODO: keep feedrate for original
        x = self.get_corresponding_x(toolhead_pos, next_toolhead_pos, target_y)
        mid_pos = Point(x, target_y)
        self.write_gcode_to_file("; Left segmented sequence start")
        print("  ! Segmented sequence")
        print("  ! Doing first part of move sequence")
        # TODO do first half of orignal print move
        self.t1_go_to(mid_pos) # no activation needed
        print("  ! Backing up t0")
        self.t1_backoff(Point(0,0)) # activation needed
        print("  ! Shuffling inactive 0")
        left_toolhead_pos = self.t0_shuffle(inactive_toolhead_pos)
        print("  ! Restoring t1 to mid_pos after backup: %s" % mid_pos)
        self.t1_go_to_w_a(mid_pos)
        print("  ! Doing second part of move sequence : %s to %s"  % (mid_pos,next_toolhead_pos ))
        # TODO resume orignal print move
        self.t1_go_to(next_toolhead_pos) # no activation needed
        self.write_gcode_to_file("; Left segmented sequence end")
        return left_toolhead_pos

    def do_left_backup_sequence(self, toolhead_pos, inactive_toolhead_pos, line):
        self.write_gcode_to_file("; Left backup sequence start")
        print("  ! Backup sequence ")
        print("  ! Backup shuffle: t1 Backing up")
        self.t1_backoff(Point(0,0)) # no activation needed
        print("  ! Shuffling inactive t0")
        left_toolhead_pos = self.t0_shuffle(inactive_toolhead_pos)
        # Restore original x for active instance
        print("  ! Resuming after backup: t1 restoring to %s" % toolhead_pos)
        self.t1_go_to_w_a(toolhead_pos)
        print("  ! Running original move.")
        self.write_gcode_to_file(line.gcode_str)
        self.write_gcode_to_file("; Left backup sequence end")
        return left_toolhead_pos

    def play_gcodes_file(self, gcode_file):
        with open(gcode_file, 'r') as f:
            file_content = f.read()
        f.close()
        # now open output. which could be the same as input
        self.output = open(self.output_filename, "w")  # reset given output file
        if self.output is None:
            print("Could not create output file: %s" % self.output_filename)
            sys.exit(1)

        self.play_gcodes(file_content)

    def play_gcodes(self, input_file_content):
        """Execute all G-codes from file content, inserting backups/shuffles/splits as needed."""
        lines = GcodeParser(input_file_content, include_comments=True).lines

        right_toolhead_pos = Point(X_HIGH, Y_HIGH)  # self.t1_park()
        left_toolhead_pos = Point(X_LOW, Y_HIGH) # self.t0_park()
        active_instance: str = 'left'

        for line in lines:
            print("pos T0: X:%.1f Y:%.1f" % (left_toolhead_pos.x, left_toolhead_pos.y))
            print("pos T1: X:%.1f Y:%.1f" % (right_toolhead_pos.x, right_toolhead_pos.y))
            print("input : " + line.gcode_str)


            if self.is_toolchange_gcode(line):

                # Decide on action     
                if line.command == T0.command:
                    if active_instance == 'left':
                        print( "Tool already active")
                    else:
                        if PP_comment in line.comment :
                            # Just swap active instance without adding additional gcode
                            print("Tool activation was inserted by PostProcessing.")
                            active_instance = 'left'
                        else:
                            right_toolhead_pos = self.t1_park()
                            active_instance = 'left'
                elif line.command == T1.command:
                    if active_instance == 'right':
                        print("Tool already active")
                    else:
                        if PP_comment in line.comment :
                            # Just swap active instance without adding additional gcode
                            print("Tool activation was inserted by PostProcessing.")
                            active_instance = 'right'
                        else:
                            # debug info
                            print("  *   parking currently active toolhead (%s) " % active_instance)
                            # end debug info
                            left_toolhead_pos = self.t0_park()
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

            elif self.is_move_gcode(line):

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

                # extract more parameter from the move like E and F


                # Ensure move is safe.
                # (1) Check against destination bounding box.
                overlap_rect = check_for_overlap(inactive_toolhead_pos, next_toolhead_pos)
                if overlap_rect:
                    print("overlap end pos: inactive: %s ,next pos: %s" %(inactive_toolhead_pos, next_toolhead_pos))

                # (2) Check swept area against inactive bounding box
                overlap_swept = check_for_overlap_sweep(toolhead_pos, next_toolhead_pos, inactive_toolhead_pos)
                if overlap_swept:
                    print("overlap swept  inactive: %s ,current pos : %s, next pos: %s" %(inactive_toolhead_pos,toolhead_pos, next_toolhead_pos))

                # Check if a single move will suffice.
                if overlap_rect or overlap_swept:
                    min_y_to_clear_inactive_toolhead = TOOLHEAD_Y_HEIGHT
                    max_y_to_clear_inactive_toolhead = Y_HEIGHT - min_y_to_clear_inactive_toolhead

                    if active_instance == 'left':
                        # Target must be on the right.

                        # Simple shuffle if we're not in the end zone yet.
                        if left_toolhead_pos.x < T0_X_BACKOFF:
                            self.simple_shuffles += 1
                            print("  ! Simple shuffle")
                            print("  ! Shuffling inactive t1")
                            right_toolhead_pos = self.t1_shuffle(right_toolhead_pos)
                            left_toolhead_pos =self.t0_activate(left_toolhead_pos)
                            self.write_gcode_to_file(line.gcode_str)

                        # Segmented move: active is now in the front, and would conflict with a back-to-front shuffled inactive toolhead
                        elif toolhead_pos.y <= min_y_to_clear_inactive_toolhead:
                            self.segmented_shuffles += 1
                            right_toolhead_pos = self.do_right_segmented_sequence(left_toolhead_pos, min_y_to_clear_inactive_toolhead,
                                                                                  next_toolhead_pos, right_toolhead_pos)

                        # Segmented move: active is in the rear
                        elif toolhead_pos.y >= max_y_to_clear_inactive_toolhead:
                            self.segmented_shuffles += 1
                            right_toolhead_pos = self.do_right_segmented_sequence(left_toolhead_pos, max_y_to_clear_inactive_toolhead,
                                                                                  next_toolhead_pos, right_toolhead_pos)

                        # Backup move.
                        else:
                            self.backup_shuffles += 1
                            right_toolhead_pos = self.do_right_backup_sequence(left_toolhead_pos, right_toolhead_pos, line)

                    elif active_instance == 'right':
                        # Target must be on the left.

                        # Simple shuffle if we're not in the end zone yet.
                        if toolhead_pos.x > X_BACKOFF_LEN:
                            self.simple_shuffles += 1
                            print("  ! Simple shuffle")
                            print("  ! Shuffling inactive t0")
                            left_toolhead_pos = self.t0_shuffle(left_toolhead_pos)
                            right_toolhead_pos = self.t1_activate(right_toolhead_pos)
                            self.write_gcode_to_file(line.gcode_str)

                        # Segmented move: active is in the front
                        elif toolhead_pos.y <= min_y_to_clear_inactive_toolhead:
                            self.segmented_shuffles += 1
                            left_toolhead_pos = self.do_left_segmented_sequence(right_toolhead_pos, min_y_to_clear_inactive_toolhead,
                                                                                  next_toolhead_pos, left_toolhead_pos)

                        # Segmented move: active is in the rear
                        elif toolhead_pos.y >= max_y_to_clear_inactive_toolhead:
                            self.segmented_shuffles += 1
                            left_toolhead_pos = self.do_left_segmented_sequence(right_toolhead_pos, max_y_to_clear_inactive_toolhead,
                                                                                  next_toolhead_pos, left_toolhead_pos)

                        # Backup move.
                        else:
                            self.backup_shuffles += 1
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
        if args.input and not os.path.exists(args.input):
            print("Invalid input file path: %s" % args.input)
            sys.exit(1)

        print("Running:")
        if args.gcodefile:
            self.play_gcodes_file(args.gcodefile)
        elif args.input:
            self.play_gcodes_file(args.input)

        if self.output:
            self.output.close()

        print("Finished.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Dual Gantry printer.")
    #parser.add_argument('--home', help="Home first", action='store_true')
    #parser.add_argument('--home-after', help="Home after print", action='store_true')
    parser.add_argument('--input', help="Input gcode filepath")
    parser.add_argument('--output', help="Output gcode filepath")
    #parser.add_argument('--verbose', help="Use more-verbose debug output", action='store_true')
    parser.add_argument('gcodefile', nargs='?')

    args = parser.parse_args()

    dr = DuelRunner(args)
    dr.run()