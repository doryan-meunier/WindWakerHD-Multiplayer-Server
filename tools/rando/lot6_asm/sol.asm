; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,
; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.
; Converti par tools/wwmp/rando_lot6.py (--porter) avec tools/wwmp/rando_port_twwhd.py.
; FICHIER GENERE : ne pas le modifier a la main. Utilise SEULEMENT par le pack WWMP_Rando genere.

; [symboles]
0x02358A90 = wwr_jeu_02358A90:
0x025D8120 = wwr_jeu_025D8120:
0x025D8870 = wwr_jeu_025D8870:
; [cave]
wwr_sol_028FA4A0:    ; create_pot_item
    .int 0x9421FFF0              ; stwu r1, -16(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010014              ; stw r0, 20(r1)
    .int 0xA17B0798              ; lhz r11, 1944(r27)
    .int 0x556BC63E              ; rlwinm r11, r11, 24, 24, 31
    .int 0x2C0B00FF              ; cmpwi r11, 255
    .int 0x41820020              ; beq 0x028FA4D8
    .int 0x2C0B0000              ; cmpwi r11, 0
    .int 0x41820018              ; beq 0x028FA4D8
    .int 0x7D645B78              ; mr r4, r11
    .int 0x38E00003              ; li r7, 3
    .int 0x39200005              ; li r9, 5
    bl wwr_jeu_025D8870      ; 4BCDE3A1
    b wwr_sol_028FA4DC           ; 48000008
    bl wwr_jeu_025D8120      ; 4BCDDC49
wwr_sol_028FA4DC:
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
wwr_sol_028FA4EC:    ; create_stone_head_item
    .int 0xA17F02F8              ; lhz r11, 760(r31)
    .int 0x556B063E              ; rlwinm r11, r11, 0, 24, 31
    .int 0x2C0B00FF              ; cmpwi r11, 255
    .int 0x41820020              ; beq 0x028FA518
    .int 0x2C0B0000              ; cmpwi r11, 0
    .int 0x41820018              ; beq 0x028FA518
    .int 0x7D645B78              ; mr r4, r11
    .int 0x38E00003              ; li r7, 3
    .int 0x39200005              ; li r9, 5
    bl wwr_jeu_025D8870      ; 4BCDE361
    b wwr_jeu_02358A90       ; 4BA5E57C
    bl wwr_jeu_025D8120      ; 4BCDDC09
    b wwr_jeu_02358A90       ; 4BA5E574
; [sites]
0x024CC08C = bl wwr_sol_028FA4A0          ; twwhd avant=4810C095 apres=4842E415
0x024CC134 = bl wwr_sol_028FA4A0          ; twwhd avant=4810BFED apres=4842E36D
0x02358A54 = ori r0, r0, 0                ; twwhd avant=4182003C apres=60000000
0x02358A8C = b wwr_sol_028FA4EC           ; twwhd avant=4827F695 apres=485A1A60
