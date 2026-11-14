; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,
; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.
; Converti par tools/wwmp/rando_lot8.py (--porter) avec tools/wwmp/rando_port_twwhd.py.
; FICHIER GENERE : ne pas le modifier a la main. Utilise SEULEMENT par le pack WWMP_Rando genere.

; [symboles]
0x021271A4 = wwr_jeu_021271A4:
0x021271AC = wwr_jeu_021271AC:
0x02129328 = wwr_jeu_02129328:
0x02129330 = wwr_jeu_02129330:
0x021A4318 = wwr_jeu_021A4318:
0x021A454C = wwr_jeu_021A454C:
0x021A4550 = wwr_jeu_021A4550:
0x024136AC = wwr_jeu_024136AC:
0x024136D0 = wwr_jeu_024136D0:
0x0241382C = wwr_jeu_0241382C:
0x025B50DC = wwr_jeu_025B50DC:
0x02657F08 = wwr_jeu_02657F08:
; [cave]
wwr8_entrees_028F9B24:    ; check_entering_door10_forwards
    .int 0x881F03BC              ; lbz r0, 956(r31)
    .int 0x2C000000              ; cmpwi r0, 0
    .int 0x41820008              ; beq 0x028F9B34
    b wwr_jeu_021271AC  ; 4B82D67C
    .int 0x880907B8              ; lbz r0, 1976(r9)
    b wwr_jeu_021271A4  ; 4B82D66C
wwr8_entrees_028F9B3C:    ; check_entering_door12_forwards
    .int 0x881F03BC              ; lbz r0, 956(r31)
    .int 0x2C000000              ; cmpwi r0, 0
    .int 0x41820008              ; beq 0x028F9B4C
    b wwr_jeu_02129330  ; 4B82F7E8
    .int 0x898C07B8              ; lbz r12, 1976(r12)
    b wwr_jeu_02129328  ; 4B82F7D8
wwr8_entrees_028F9B54:    ; check_leaving_cabana
    .int 0x881F03BC              ; lbz r0, 956(r31)
    .int 0x2C000000              ; cmpwi r0, 0
    .int 0x41820020              ; beq 0x028F9B7C
    .int 0x38600001              ; li r3, 1
    .int 0x987F05A4              ; stb r3, 1444(r31)
    .int 0x7FE3FB78              ; mr r3, r31
    bl wwr_jeu_021A4318 ; 4B8AA7AD
    .int 0x38600008              ; li r3, 8
    .int 0x987F05A4              ; stb r3, 1444(r31)
    b wwr_jeu_021A4550  ; 4B8AA9D8
    .int 0x7FE3FB78              ; mr r3, r31
    b wwr_jeu_021A454C  ; 4B8AA9CC
wwr8_entrees_028F9B84:    ; set_return_place_as_last_visited_ocean_sector
    .int 0x3CA01020              ; lis r5, 0x1020
    .int 0x80A58344              ; lwz r5, -31932(r5)
    .int 0x80A50218              ; lwz r5, 536(r5)
    .int 0x88A5003E              ; lbz r5, 62(r5)
    .int 0x38A50001              ; addi r5, r5, 1
    b wwr_jeu_025B50DC  ; 4BCBB544
wwr8_entrees_028F9F18:    ; boat_spawn_fallback_check
    .int 0x40820014              ; bne 0x028F9F2C
    .int 0x2C180005              ; cmpwi r24, 5
    .int 0x40820008              ; bne 0x028F9F28
    b wwr_jeu_0241382C  ; 4BB19908
    b wwr_jeu_024136AC  ; 4BB19784
    b wwr_jeu_024136D0  ; 4BB197A4
wwr8_entrees_028F9F30:    ; force_map_reload
    .int 0x7C09582E              ; lwzx r0, r9, r11
    .int 0x81830920              ; lwz r12, 2336(r3)
    .int 0x7C006000              ; cmpw r0, r12
    .int 0x4182000C              ; beq 0x028F9F48
    .int 0x39800000              ; li r12, 0
    .int 0x91880064              ; stw r12, 100(r8)
    b wwr_jeu_02657F08  ; 4BD5DFC0
; [sites]
0x021271A0 = b wwr8_entrees_028F9B24      ; twwhd avant=880907B8 apres=487D2984
0x02129324 = b wwr8_entrees_028F9B3C      ; twwhd avant=898C07B8 apres=487D0818
0x021A4548 = b wwr8_entrees_028F9B54      ; twwhd avant=7FE3FB78 apres=4875560C
0x025220C4 = bl wwr8_entrees_028F9B84     ; twwhd avant=48093019 apres=483D7AC1
0x024136A8 = b wwr8_entrees_028F9F18      ; twwhd avant=40820028 apres=484E6870
0x02657F04 = b wwr8_entrees_028F9F30      ; twwhd avant=7C09582E apres=482A202C
