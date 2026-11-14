; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,
; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.
; Converti par tools/wwmp/rando_lot6.py (--porter) avec tools/wwmp/rando_port_twwhd.py.
; FICHIER GENERE : ne pas le modifier a la main. Utilise SEULEMENT par le pack WWMP_Rando genere.

; [symboles]
0x0246760C = wwr_jeu_0246760C:
0x024676B4 = wwr_jeu_024676B4:
0x025D8870 = wwr_jeu_025D8870:
; [cave]
wwr_tres_028F9484:    ; custom_createItem
    .int 0x9421FFF0              ; stwu r1, -16(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010014              ; stw r0, 20(r1)
    .int 0x2C0400FF              ; cmpwi r4, 255
    .int 0x41820028              ; beq 0x028F94BC
    .int 0x7CA92B78              ; mr r9, r5
    .int 0x7D054378              ; mr r5, r8
    .int 0x7CC83378              ; mr r8, r6
    .int 0x7D264B78              ; mr r6, r9
    .int 0x7CEA3B78              ; mr r10, r7
    .int 0x38E00003              ; li r7, 3
    .int 0x39200005              ; li r9, 5
    bl wwr_jeu_025D8870     ; 4BCDF3BD
    b wwr_tres_028F94C0          ; 48000008
    .int 0x3860FFFF              ; li r3, -1
wwr_tres_028F94C0:
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
; [sites]
0x024675F4 = cmpwi r31, 1                 ; twwhd avant=807C5634 apres=2C1F0001
0x024675F8 = beq wwr_jeu_0246760C    ; twwhd avant=7E84A378 apres=41820014
0x024675FC = cmpwi r31, 3                 ; twwhd avant=4814D68D apres=2C1F0003
0x02467600 = beq wwr_jeu_0246760C    ; twwhd avant=7C7F1B78 apres=4182000C
0x02467604 = cmpwi r31, 5                 ; twwhd avant=480E9ACD apres=2C1F0005
0x02467608 = bne wwr_jeu_024676B4    ; twwhd avant=2C030000 apres=408200AC
0x0246760C = addi r31, r31, 96            ; twwhd avant=418200A8 apres=3BFF0060
0x02526490 = bl wwr_tres_028F9484         ; twwhd avant=480B2621 apres=483D2FF5
0x02526494 = ori r0, r0, 0                ; twwhd avant=2C030000 apres=60000000
0x02526498 = ori r0, r0, 0                ; twwhd avant=41820010 apres=60000000
0x0252649C = ori r0, r0, 0                ; twwhd avant=800302E0 apres=60000000
0x025264A0 = ori r0, r0, 0                ; twwhd avant=60004000 apres=60000000
0x025264A4 = ori r0, r0, 0                ; twwhd avant=900302E0 apres=60000000
