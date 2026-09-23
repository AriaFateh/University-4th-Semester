; ==========================================================================
;  sum.s -- the "very simple algorithm" used to validate the CLI grading flow
;
;  Adds the 8 words stored at DMEM[16..23], writes the total to DMEM[0]
;  and also leaves it in R4, then halts.
;
;  R1 = pointer   R2 = counter   R3 = current element   R4 = running total
; ==========================================================================

        MOVI R1, 16         ; R1 = address of the first element
        MOVI R2, 8          ; R2 = how many elements are left
        MOVI R4, 0          ; R4 = 0  (running total)

loop:   CMP  R2, R0         ; SUB.S R0,R2,R0  ->  Z = (counter == 0)
        B.EQ done
        LW   R3, 0(R1)      ; R3 = MEM[R1]
        ADD  R4, R4, R3     ; total += R3
        ADDI R1, R1, 1      ; pointer++
        ADDI R2, R2, -1     ; counter--
        B    loop

done:   SW   R4, 0(R0)      ; MEM[0] = total
        HALT
