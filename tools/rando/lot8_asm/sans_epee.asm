; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,
; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.
; Converti par tools/wwmp/rando_lot8.py (--porter) avec tools/wwmp/rando_port_twwhd.py.
; FICHIER GENERE : ne pas le modifier a la main. Utilise SEULEMENT par le pack WWMP_Rando genere.

; [symboles]
0x0213AEBC = wwr_jeu_0213AEBC:
0x025200D4 = wwr_jeu_025200D4:
0x025B2034 = wwr_jeu_025B2034:
0x025B26F8 = wwr_jeu_025B26F8:
0x025B26FC = wwr_jeu_025B26FC:
; [cave]
wwr8_sans_epee_028FB624:    ; give_temporary_sword_during_ganondorf_fight_in_swordless
    bl wwr_jeu_025200D4 ; 4BC24AB1
    .int 0x38635134              ; addi r3, r3, 20788
    .int 0x3C801005              ; lis r4, 0x1005
    .int 0x3884336C              ; addi r4, r4, 13164
    .int 0x8CA30001             
    .int 0x8CC40001             
    .int 0x7C053040             
    .int 0x4082000C              ; bne 0x028FB64C
    .int 0x28050000              ; cmplwi r5, 0x0
    .int 0x4082FFEC              ; bne 0x028FB634
    .int 0x7C653051             
    .int 0x40820020              ; bne 0x028FB670
    .int 0x3FC01020              ; lis r30, 0x1020
    .int 0x83DE84DC              ; lwz r30, -31524(r30)
    .int 0x881E002E              ; lbz r0, 46(r30)
    .int 0x2C0000FF              ; cmpwi r0, 255
    .int 0x4082000C              ; bne 0x028FB670
    .int 0x3800003E              ; li r0, 62
    .int 0x981E002E              ; stb r0, 46(r30)
    bl wwr_jeu_025200D4 ; 4BC24A65
    b wwr_jeu_025B2034 ; 4BCB69C0
wwr8_sans_epee_028FB678:    ; give_temporary_sword_in_orcas_house_in_swordless
    bl wwr_jeu_025200D4 ; 4BC24A5D
    .int 0x38635134              ; addi r3, r3, 20788
    .int 0x3C801000              ; lis r4, 0x1000
    .int 0x38843D28              ; addi r4, r4, 15656
    .int 0x8CA30001             
    .int 0x8CC40001             
    .int 0x7C053040             
    .int 0x4082000C              ; bne 0x028FB6A0
    .int 0x28050000              ; cmplwi r5, 0x0
    .int 0x4082FFEC              ; bne 0x028FB688
    .int 0x7C653050              ; subf r3, r5, r6
    .int 0x2C030000              ; cmpwi r3, 0
    .int 0x40820028              ; bne 0x028FB6D0
    .int 0x3C601020              ; lis r3, 0x1020
    .int 0x806384DC              ; lwz r3, -31524(r3)
    .int 0x8803002E              ; lbz r0, 46(r3)
    .int 0x2C0000FF              ; cmpwi r0, 255
    .int 0x40820014              ; bne 0x028FB6D0
    .int 0x38000038              ; li r0, 56
    .int 0x9803002E              ; stb r0, 46(r3)
    .int 0x7C651B78              ; mr r5, r3
    b wwr_jeu_025B26FC ; 4BCB7030
    .int 0x809B84DC              ; lwz r4, -31524(r27)
    b wwr_jeu_025B26F8 ; 4BCB7024
wwr8_sans_epee_028FB6D8:    ; remove_temporary_sword_when_loading_stage_in_swordless
    .int 0x880400D4              ; lbz r0, 212(r4)
    .int 0x2C000000              ; cmpwi r0, 0
    .int 0x4082000C              ; bne 0x028FB6EC
    .int 0x380000FF              ; li r0, 255
    .int 0x9804002E              ; stb r0, 46(r4)
    .int 0x88A40068              ; lbz r5, 104(r4)
    b wwr_jeu_025B26FC ; 4BCB700C
; [sites]
0x0213AEA8 = b wwr_jeu_0213AEBC ; twwhd avant=41820014 apres=48000014
0x025B2030 = b wwr8_sans_epee_028FB624    ; twwhd avant=4BF6E0A5 apres=483495F4
0x025B26F4 = b wwr8_sans_epee_028FB678    ; twwhd avant=809B84DC apres=48348F84
0x025B26F8 = b wwr8_sans_epee_028FB6D8    ; twwhd avant=88A40068 apres=48348FE0
