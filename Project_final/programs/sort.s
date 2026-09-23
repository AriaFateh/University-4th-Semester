; ==========================================================================
;  sort.s -- bubble sort of 8 signed words at DMEM[16..23], ascending.
;
;  R1 = base   R2 = n   R3 = j pointer   R4 = a[j]   R5 = a[j+1]   R6 = end
;
;  All test values stay inside [-1000, 1000] so that SUB never overflows and
;  the N flag alone is a correct signed comparison.
; ==========================================================================

        MOVI R1, 16         ; base address of the array
        MOVI R2, 8          ; n = number of elements

outer:  ADDI R2, R2, -1     ; n = n - 1     (ADDI never touches the flags)
        CMP  R2, R0         ; Z = (n == 0)
        B.EQ done
        ADD  R3, R1, R0     ; j   = base
        ADD  R6, R1, R2     ; end = base + n

inner:  CMP  R3, R6         ; N = (j < end)
        B.LT body
        B    outer          ; this pass is finished

body:   LW   R4, 0(R3)      ; a[j]
        LW   R5, 1(R3)      ; a[j+1]
        CMP  R5, R4         ; N = (a[j+1] < a[j])
        B.LT swap
        B    next

swap:   SW   R5, 0(R3)
        SW   R4, 1(R3)

next:   ADDI R3, R3, 1
        B    inner

done:   HALT
