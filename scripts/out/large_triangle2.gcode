T0 ; handled by PPfD0
; G1 X110 Y10 F6000
; G1 X110 Y105 F6000
; G1 X10 Y105 F6000
; G1 X110 Y10 F6000
T0 ; PPfD0 t0_park
G0 X1.0 F15000
G0 Y159.0 F15000
T1 ; handled by PPfD0
G1 X160 Y10 F6000
G1 X160 Y155 F6000
; Right simple shuffle start
T0 ; PPfD0 t0_shuffle
G0 Y1.0 F15000
T1 ; PPfD0 t1_activate
G1 F6000 ; restored feed_rate by PPfD0
; Right simple shuffle end
G1 X10 Y155 F6000
G1 X160 Y10 F6000
