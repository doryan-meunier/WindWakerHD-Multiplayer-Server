; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,
; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.
; Converti par tools/wwmp/rando_lot6.py (--porter) avec tools/wwmp/rando_port_twwhd.py.
; FICHIER GENERE : ne pas le modifier a la main. Utilise SEULEMENT par le pack WWMP_Rando genere.

; [symboles]
0x025B8B68 = wwr_jeu_025B8B68:
0x025B8B94 = wwr_jeu_025B8B94:
0x025D7A58 = wwr_jeu_025D7A58:
; [cave]
wwr_evt_028F8E1C:    ; isEventBit_wrapper
    .int 0x9421FFF0              ; stwu r1, -16(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010014              ; stw r0, 20(r1)
    .int 0x7C641B78              ; mr r4, r3
    .int 0x3C601020              ; lis r3, 0x1020
    .int 0x806384DC              ; lwz r3, -31524(r3)
    .int 0x38630644              ; addi r3, r3, 1604
    bl wwr_jeu_025B8B94      ; 4BCBFD5D
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
wwr_evt_028FA388:    ; rito_cafe_postman_start_event_and_set_event_bit
    .int 0x9421FFF0              ; stwu r1, -16(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010014              ; stw r0, 20(r1)
    .int 0x93E1000C              ; stw r31, 12(r1)
    .int 0x7C7F1B78              ; mr r31, r3
    bl wwr_jeu_025D7A58      ; 4BCDD6BD
    .int 0xABFF09DA              ; lha r31, 2522(r31)
    .int 0x2C1F0000              ; cmpwi r31, 0
    .int 0x40820020              ; bne 0x028FA3C8
    .int 0x7C7F1B78              ; mr r31, r3
    .int 0x3C601020              ; lis r3, 0x1020
    .int 0x806384DC              ; lwz r3, -31524(r3)
    .int 0x38630644              ; addi r3, r3, 1604
    .int 0x38806A02              ; li r4, 27138
    bl wwr_jeu_025B8B68      ; 4BCBE7A9
    .int 0x7FE3FB78              ; mr r3, r31
    .int 0x83E1000C              ; lwz r31, 12(r1)
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
; [sites]
0x021FCCD4 = li r3, 27138                 ; twwhd avant=3880000F apres=38606A02
0x021FCCD8 = ori r0, r0, 0                ; twwhd avant=386B00B0 apres=60000000
0x021FCCE0 = bl wwr_evt_028F8E1C          ; twwhd avant=483BAB61 apres=486FC13D
0x022022F0 = li r3, 27138                 ; twwhd avant=3880000F apres=38606A02
0x022022F4 = ori r0, r0, 0                ; twwhd avant=386C00B0 apres=60000000
0x022022F8 = bl wwr_evt_028F8E1C          ; twwhd avant=483B5549 apres=486F6B25
0x02200D54 = b wwr_evt_028FA388           ; twwhd avant=483D6D04 apres=486F9634
