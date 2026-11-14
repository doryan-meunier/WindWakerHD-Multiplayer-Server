; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,
; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.
; Converti par tools/wwmp/rando_lot6.py (--porter) avec tools/wwmp/rando_port_twwhd.py.
; FICHIER GENERE : ne pas le modifier a la main. Utilise SEULEMENT par le pack WWMP_Rando genere.

; [symboles]
0x0227D240 = wwr_jeu_0227D240:
0x02346F2C = wwr_jeu_02346F2C:
0x023472BC = wwr_jeu_023472BC:
0x02347C40 = wwr_jeu_02347C40:
0x02347CD4 = wwr_jeu_02347CD4:
0x025B8B68 = wwr_jeu_025B8B68:
0x025B8B94 = wwr_jeu_025B8B94:
0x025D54C4 = wwr_jeu_025D54C4:
0x025D7DEC = wwr_jeu_025D7DEC:
0x025D8870 = wwr_jeu_025D8870:
; [cave]
wwr_pnj_028F8E1C:    ; isEventBit_wrapper
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
wwr_pnj_028F9484:    ; custom_createItem
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
    bl wwr_jeu_025D8870      ; 4BCDF3BD
    b wwr_pnj_028F94C0           ; 48000008
    .int 0x3860FFFF              ; li r3, -1
wwr_pnj_028F94C0:
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
wwr_pnj_028FA03C:    ; custom_createItem_return_check
    .int 0x7C7E1B78              ; mr r30, r3
    .int 0x2C1EFFFF              ; cmpwi r30, -1
    .int 0x41820010              ; beq 0x028FA054
    .int 0x7FCAF378              ; mr r10, r30
    .int 0x915F03A4              ; stw r10, 932(r31)
    b wwr_jeu_02347C40       ; 4BA4DBF0
    b wwr_jeu_02347CD4       ; 4BA4DC80
wwr_pnj_028FA058:    ; initialize_actor_id
    .int 0x3860FFFF              ; li r3, -1
    .int 0x907E03A4              ; stw r3, 932(r30)
    .int 0x7FC3F378              ; mr r3, r30
    b wwr_jeu_02346F2C       ; 4BA4CEC8
wwr_pnj_028FA068:    ; create_item_for_withered_trees_without_setting_speeds
    .int 0x9421FFF0              ; stwu r1, -16(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010014              ; stw r0, 20(r1)
    bl wwr_pnj_028F9484          ; 4BFFF411
    .int 0x907C03A4              ; stw r3, 932(r28)
    .int 0x38A00001              ; li r5, 1
    .int 0x98BC032E              ; stb r5, 814(r28)
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
wwr_pnj_028FA094:    ; withered_tree_item_try_give_momentum
    .int 0x9421FFE0              ; stwu r1, -32(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010024              ; stw r0, 36(r1)
    .int 0x93C10018              ; stw r30, 24(r1)
    .int 0x93E1001C              ; stw r31, 28(r1)
    .int 0x7C7E1B78              ; mr r30, r3
    .int 0x807E03A4              ; lwz r3, 932(r30)
    .int 0x3C830001             
    .int 0x2804FFFF              ; cmplwi r4, 0xFFFF
    .int 0x41820088              ; beq 0x028FA140
    .int 0x38810008              ; addi r4, r1, 8
    bl wwr_jeu_025D54C4      ; 4BCDB405
    .int 0x2C030000              ; cmpwi r3, 0
    .int 0x4182005C              ; beq 0x028FA124
    .int 0x80810008              ; lwz r4, 8(r1)
    .int 0x2C040000              ; cmpwi r4, 0
    .int 0x4182006C              ; beq 0x028FA140
    .int 0x815E0004              ; lwz r10, 4(r30)
    .int 0x914402E8              ; stw r10, 744(r4)
    .int 0x88BE032E              ; lbz r5, 814(r30)
    .int 0x2C050000              ; cmpwi r5, 0
    .int 0x40820058              ; bne 0x028FA140
    lis r10, wwr_pnj_028FA160@ha ; 3D400290
    addi r10, r10, wwr_pnj_028FA160@l ; 394AA160
    .int 0xC00A0000              ; lfs f0, 0(r10)
    .int 0xD0040370              ; stfs f0, 880(r4)
    .int 0xC00A0004              ; lfs f0, 4(r10)
    .int 0xD0040340              ; stfs f0, 832(r4)
    .int 0xC00A0008              ; lfs f0, 8(r10)
    .int 0xD0040374              ; stfs f0, 884(r4)
    .int 0x80A402E0              ; lwz r5, 736(r4)
    .int 0x60A50040              ; ori r5, r5, 0x40
    .int 0x90A402E0              ; stw r5, 736(r4)
    .int 0x38A00001              ; li r5, 1
    .int 0x98BE032E              ; stb r5, 814(r30)
    b wwr_pnj_028FA140           ; 48000020
    .int 0x3C601020              ; lis r3, 0x1020
    .int 0x806384DC              ; lwz r3, -31524(r3)
    .int 0x38630644              ; addi r3, r3, 1604
    .int 0x38802E20              ; li r4, 11808
    bl wwr_jeu_025B8B68      ; 4BCBEA35
    .int 0x3800FFFF              ; li r0, -1
    .int 0x901E03A4              ; stw r0, 932(r30)
wwr_pnj_028FA140:
    .int 0x7FC3F378              ; mr r3, r30
    bl wwr_jeu_023472BC      ; 4BA4D179
    .int 0x80010024              ; lwz r0, 36(r1)
    .int 0x83C10018              ; lwz r30, 24(r1)
    .int 0x83E1001C              ; lwz r31, 28(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210020              ; addi r1, r1, 32
    .int 0x4E800020              ; blr
wwr_pnj_028FA160:    ; withered_tree_item_speeds
    .int 0x3FE00000              ; lis r31, 0x0
    .int 0x41F00000             
    .int 0xC0066666              ; lfs f0, 26214(r6)
wwr_pnj_028FA2A0:    ; zunari_give_item_and_set_magic_armor_event_bit
    .int 0x9421FFF0              ; stwu r1, -16(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010014              ; stw r0, 20(r1)
    .int 0x93E1000C              ; stw r31, 12(r1)
    .int 0x7C9F2378              ; mr r31, r4
    bl wwr_jeu_025D7DEC      ; 4BCDDB39
    lis r4, wwr_pnj_028FA2FC@ha  ; 3C800290
    addi r4, r4, wwr_pnj_028FA2FC@l ; 3884A2FC
    .int 0x88840000              ; lbz r4, 0(r4)
    .int 0x7C1F2000              ; cmpw r31, r4
    .int 0x40820020              ; bne 0x028FA2E8
    .int 0x7C7F1B78              ; mr r31, r3
    .int 0x3C601020              ; lis r3, 0x1020
    .int 0x806384DC              ; lwz r3, -31524(r3)
    .int 0x38630644              ; addi r3, r3, 1604
    .int 0x38806940              ; li r4, 26944
    bl wwr_jeu_025B8B68      ; 4BCBE889
    .int 0x7FE3FB78              ; mr r3, r31
    .int 0x83E1000C              ; lwz r31, 12(r1)
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
wwr_pnj_028FA2FC:    ; zunari_magic_armor_slot_item_id
    {{zunari}}
wwr_pnj_028FA344:    ; maggie_give_item_and_set_event_bit
    .int 0x9421FFF0              ; stwu r1, -16(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010014              ; stw r0, 20(r1)
    .int 0x93E1000C              ; stw r31, 12(r1)
    bl wwr_jeu_025D7DEC      ; 4BCDDA99
    .int 0x7C7F1B78              ; mr r31, r3
    .int 0x3C601020              ; lis r3, 0x1020
    .int 0x806384DC              ; lwz r3, -31524(r3)
    .int 0x38630644              ; addi r3, r3, 1604
    .int 0x38806A01              ; li r4, 27137
    bl wwr_jeu_025B8B68      ; 4BCBE7FD
    .int 0x7FE3FB78              ; mr r3, r31
    .int 0x83E1000C              ; lwz r31, 12(r1)
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
; [sites]
0x022D94E8 = li r3, 26944                 ; twwhd avant=3860002A apres=38606940
0x022D94EC = ori r0, r0, 0                ; twwhd avant=38800001 apres=60000000
0x022D94F0 = bl wwr_pnj_028F8E1C          ; twwhd avant=48274561 apres=4861F92D
0x022DA8A4 = bl wwr_pnj_028FA2A0          ; twwhd avant=482FD549 apres=4861F9FD
0x0227D244 = li r3, 27137                 ; twwhd avant=3880000E apres=38606A01
0x0227D248 = ori r0, r0, 0                ; twwhd avant=386500B0 apres=60000000
0x0227D24C = bl wwr_pnj_028F8E1C          ; twwhd avant=4833A5F5 apres=4867BBD1
0x0227C2B8 = bl wwr_pnj_028FA344          ; twwhd avant=4835BB35 apres=4867E08D
0x0227D200 = b wwr_jeu_0227D240       ; twwhd avant=40820040 apres=48000040
0x02347C34 = bl wwr_pnj_028F9484          ; twwhd avant=48290E7D apres=485B1851
0x02347C38 = b wwr_pnj_028FA03C           ; twwhd avant=7C7E1B79 apres=485B2404
0x02347C48 = ori r0, r0, 0                ; twwhd avant=915E02E8 apres=60000000
0x02347CB4 = ori r0, r0, 0                ; twwhd avant=913E02E0 apres=60000000
0x02346F28 = b wwr_pnj_028FA058           ; twwhd avant=7FC3F378 apres=485B3130
0x02347ABC = bl wwr_pnj_028FA068          ; twwhd avant=48290FF5 apres=485B25AD
0x02347AC0 = cmpwi r3, -1                 ; twwhd avant=2C030000 apres=2C03FFFF
0x02347ACC = ori r0, r0, 0                ; twwhd avant=914302E8 apres=60000000
0x02347D18 = bl wwr_pnj_028FA094          ; twwhd avant=4BFFF5A5 apres=485B237D
