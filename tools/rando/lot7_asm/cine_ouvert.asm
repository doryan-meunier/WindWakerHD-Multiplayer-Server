; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,
; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.
; Converti par tools/wwmp/rando_lot7.py (--porter) avec tools/wwmp/rando_port_twwhd.py.
; FICHIER GENERE : ne pas le modifier a la main. Utilise SEULEMENT par le pack WWMP_Rando genere.

; [symboles]
0x02299860 = wwr_jeu_02299860:
0x02336D94 = wwr_jeu_02336D94:
0x02520A84 = wwr_jeu_02520A84:
0x025AEE18 = wwr_jeu_025AEE18:
0x025B7CC0 = wwr_jeu_025B7CC0:
0x025B7E00 = wwr_jeu_025B7E00:
0x025B8B68 = wwr_jeu_025B8B68:
; [cave]
wwr7_cine_ouvert_028FADA8:    ; check_hyrule_warp_unlocked
    .int 0x9421FFF0              ; stwu r1, -16(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010014              ; stw r0, 20(r1)
    .int 0x3C601020              ; lis r3, 0x1020
    .int 0x806384DC              ; lwz r3, -31524(r3)
    .int 0x386300D4              ; addi r3, r3, 212
    bl wwr_jeu_025B7E00 ; 4BCBD041
    .int 0x2C030008              ; cmpwi r3, 8
    .int 0x4080000C              ; bge 0x028FADD4
    .int 0x38600000              ; li r3, 0
    b wwr7_cine_ouvert_028FADD8  ; 48000008
    .int 0x38600001              ; li r3, 1
wwr7_cine_ouvert_028FADD8:
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
wwr7_cine_ouvert_028FADE8:    ; give_pearl_and_raise_totg_if_necessary
    .int 0x9421FFF0              ; stwu r1, -16(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010014              ; stw r0, 20(r1)
    .int 0x93E1000C              ; stw r31, 12(r1)
    .int 0x7C9F2378              ; mr r31, r4
    bl wwr_jeu_025B7CC0 ; 4BCBCEC5
    .int 0x3C601020              ; lis r3, 0x1020
    .int 0x806384DC              ; lwz r3, -31524(r3)
    .int 0x38630644              ; addi r3, r3, 1604
    .int 0x2C1F0000              ; cmpwi r31, 0
    .int 0x41820018              ; beq 0x028FAE28
    .int 0x2C1F0001              ; cmpwi r31, 1
    .int 0x4182001C              ; beq 0x028FAE34
    .int 0x2C1F0002              ; cmpwi r31, 2
    .int 0x41820020              ; beq 0x028FAE40
    b wwr7_cine_ouvert_028FAE48  ; 48000024
    .int 0x38801410              ; li r4, 5136
    bl wwr_jeu_025B8B68 ; 4BCBDD3D
    b wwr7_cine_ouvert_028FAE48  ; 48000018
    .int 0x38801480              ; li r4, 5248
    bl wwr_jeu_025B8B68 ; 4BCBDD31
    b wwr7_cine_ouvert_028FAE48  ; 4800000C
    .int 0x38801440              ; li r4, 5184
    bl wwr_jeu_025B8B68 ; 4BCBDD25
wwr7_cine_ouvert_028FAE48:
    .int 0x3CA01020              ; lis r5, 0x1020
    .int 0x80A584DC              ; lwz r5, -31524(r5)
    .int 0x38A500DF              ; addi r5, r5, 223
    .int 0x88850000              ; lbz r4, 0(r5)
    .int 0x2C040007              ; cmpwi r4, 7
    .int 0x40820014              ; bne 0x028FAE70
    .int 0x38801E40              ; li r4, 7744
    bl wwr_jeu_025B8B68 ; 4BCBDD05
    .int 0x38802E80              ; li r4, 11904
    bl wwr_jeu_025B8B68 ; 4BCBDCFD
    .int 0x83E1000C              ; lwz r31, 12(r1)
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
; [sites]
0x025AEDCC = ori r0, r0, 0                ; twwhd avant=4081001C apres=60000000
0x025AEDE0 = ori r0, r0, 0                ; twwhd avant=41820008 apres=60000000
0x025AEE00 = b wwr_jeu_025AEE18 ; twwhd avant=41820018 apres=48000018
0x024D602C = li r3, 1                     ; twwhd avant=7C0802A6 apres=38600001
0x024D6030 = blr                          ; twwhd avant=9421FFF0 apres=4E800020
0x02336D68 = b wwr_jeu_02336D94 ; twwhd avant=A89E0B96 apres=4800002C
0x024D4EC0 = bl wwr7_cine_ouvert_028FADA8 ; twwhd avant=480E3CD5 apres=48425EE9
0x024D53D0 = bl wwr7_cine_ouvert_028FADA8 ; twwhd avant=480E37C5 apres=484259D9
0x023A421C = li r3, 4                     ; twwhd avant=38690644 apres=38600004
0x023A4220 = bl wwr_jeu_02520A84 ; twwhd avant=48214975 apres=4817C865
0x025542E0 = li r3, 2                     ; twwhd avant=38690644 apres=38600002
0x025542E4 = bl wwr_jeu_02520A84 ; twwhd avant=480648B1 apres=4BFCC7A1
0x02554308 = li r3, 2                     ; twwhd avant=386B0644 apres=38600002
0x0255430C = bl wwr_jeu_02520A84 ; twwhd avant=48064889 apres=4BFCC779
0x024D4F28 = ori r0, r0, 0                ; twwhd avant=480B1F6D apres=60000000
0x0254F52C = b wwr7_cine_ouvert_028FADE8  ; twwhd avant=48068794 apres=483AB8BC
0x0254F540 = b wwr7_cine_ouvert_028FADE8  ; twwhd avant=48068780 apres=483AB8A8
0x0254F554 = b wwr7_cine_ouvert_028FADE8  ; twwhd avant=4806876C apres=483AB894
0x0207FBFC = li r6, -1                    ; twwhd avant=38C00009 apres=38C0FFFF
0x02299808 = b wwr_jeu_02299860 ; twwhd avant=41820058 apres=48000058
0x022400AC = li r4, 8064                  ; twwhd avant=38801E04 apres=38801F80
0x02240688 = li r4, 8064                  ; twwhd avant=38801E04 apres=38801F80
0x0221F530 = li r3, 1                     ; twwhd avant=48301555 apres=38600001
