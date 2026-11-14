; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,
; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.
; Converti par tools/wwmp/rando_lot6.py (--porter) avec tools/wwmp/rando_port_twwhd.py.
; FICHIER GENERE : ne pas le modifier a la main. Utilise SEULEMENT par le pack WWMP_Rando genere.

; [symboles]
0x02212C9C = wwr_jeu_02212C9C:
0x02212CAC = wwr_jeu_02212CAC:
0x02215630 = wwr_jeu_02215630:
0x025B8B68 = wwr_jeu_025B8B68:
0x025B8B94 = wwr_jeu_025B8B94:
0x025BCECC = wwr_jeu_025BCECC:
; [cave]
wwr_beedle_028F9F88:    ; set_shop_item_in_bait_bag_slot_sold_out
    .int 0x9421FFF0              ; stwu r1, -16(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010014              ; stw r0, 20(r1)
    bl wwr_jeu_025BCECC   ; 4BCC2F39
    .int 0x3C601020              ; lis r3, 0x1020
    .int 0x806384DC              ; lwz r3, -31524(r3)
    .int 0x38630644              ; addi r3, r3, 1604
    .int 0x38806902              ; li r4, 26882
    bl wwr_jeu_025B8B68   ; 4BCBEBC1
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
wwr_beedle_028F9FBC:    ; check_shop_item_in_bait_bag_slot_sold_out
    .int 0x9421FFF0              ; stwu r1, -16(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010014              ; stw r0, 20(r1)
    .int 0x3C601020              ; lis r3, 0x1020
    .int 0x806384DC              ; lwz r3, -31524(r3)
    .int 0x38630644              ; addi r3, r3, 1604
    .int 0x38806902              ; li r4, 26882
    bl wwr_jeu_025B8B94   ; 4BCBEBBD
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
wwr_beedle_028F9FEC:    ; check_buying_beedle_20_item
    .int 0x9421FFF0              ; stwu r1, -16(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010014              ; stw r0, 20(r1)
    .int 0xA8630000              ; lha r3, 0(r3)
    .int 0x2C030000              ; cmpwi r3, 0
    .int 0x40820028              ; bne 0x028FA028
    .int 0x887B099F              ; lbz r3, 2463(r27)
    .int 0x28030004              ; cmplwi r3, 0x4
    .int 0x4182001C              ; beq 0x028FA028
    .int 0x28030005              ; cmplwi r3, 0x5
    .int 0x41820014              ; beq 0x028FA028
    bl wwr_beedle_028F9FBC       ; 4BFFFFA5
    .int 0x2C030000              ; cmpwi r3, 0
    .int 0x38600001              ; li r3, 1
    .int 0x41820008              ; beq 0x028FA02C
    .int 0x38600000              ; li r3, 0
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
; [sites]
0x02212C64 = mr r20, r23                  ; twwhd avant=2C13002C apres=7EF4BB78
0x02212C68 = lbz r3, 2463(r26)            ; twwhd avant=7EF4BB78 apres=887A099F
0x02212C6C = cmplwi r3, 0x4               ; twwhd avant=40820018 apres=28030004
0x02212C70 = beq wwr_jeu_02212CAC  ; twwhd avant=3860002C apres=4182003C
0x02212C74 = cmplwi r3, 0x5               ; twwhd avant=4830DF99 apres=28030005
0x02212C78 = beq wwr_jeu_02212CAC  ; twwhd avant=2C030000 apres=41820034
0x02212C7C = bl wwr_beedle_028F9FBC       ; twwhd avant=41820030 apres=486E7341
0x02212C80 = cmpwi r3, 0                  ; twwhd avant=4800001C apres=2C030000
0x02212C84 = beq wwr_jeu_02212CAC  ; twwhd avant=2C130083 apres=41820028
0x02212C88 = b wwr_jeu_02212C9C    ; twwhd avant=40820024 apres=48000014
0x0221543C = bl wwr_beedle_028F9FEC       ; twwhd avant=483A7DF1 apres=486E4BB1
0x02215440 = cmpwi r3, 1                  ; twwhd avant=2C03002C apres=2C030001
0x02215450 = bl wwr_beedle_028F9F88       ; twwhd avant=483A7A7D apres=486E4B39
0x02215570 = lha r3, 2368(r27)            ; twwhd avant=387B0940 apres=A87B0940
0x02215574 = li r4, 32                    ; twwhd avant=483A7CB9 apres=38800020
0x02215578 = srw r4, r4, r3               ; twwhd avant=7C7F1B78 apres=7C841C30
0x0221557C = addi r4, r4, 8192            ; twwhd avant=2C1F0050 apres=38842000
0x02215580 = lwz r3, -31524(r28)          ; twwhd avant=41820014 apres=807C84DC
0x02215584 = addi r3, r3, 1604            ; twwhd avant=2C1F0007 apres=38630644
0x02215588 = bl wwr_jeu_025B8B94   ; twwhd avant=4182000C apres=483A360D
0x0221558C = cmplwi r3, 0x0               ; twwhd avant=2C1F00E1 apres=28030000
0x02215590 = bne wwr_jeu_02215630  ; twwhd avant=408200A0 apres=408200A0
0x022155AC = cmpwi r0, 1                  ; twwhd avant=281F0007 apres=2C000001
0x022155B8 = cmpwi r0, 0                  ; twwhd avant=281F0050 apres=2C000000
0x022155C0 = cmpwi r0, 2                  ; twwhd avant=281F00E1 apres=2C000002
