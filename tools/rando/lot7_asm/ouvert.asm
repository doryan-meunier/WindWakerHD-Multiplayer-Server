; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,
; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.
; Converti par tools/wwmp/rando_lot7.py (--porter) avec tools/wwmp/rando_port_twwhd.py.
; FICHIER GENERE : ne pas le modifier a la main. Utilise SEULEMENT par le pack WWMP_Rando genere.

; [symboles]
0x02026D60 = wwr_jeu_02026D60:
0x02190F0C = wwr_jeu_02190F0C:
0x021A4C28 = wwr_jeu_021A4C28:
0x021CA210 = wwr_jeu_021CA210:
0x021CD298 = wwr_jeu_021CD298:
0x0221F8EC = wwr_jeu_0221F8EC:
0x02227DE0 = wwr_jeu_02227DE0:
0x0222CA1C = wwr_jeu_0222CA1C:
0x022863E8 = wwr_jeu_022863E8:
0x022C7A70 = wwr_jeu_022C7A70:
0x022C7D70 = wwr_jeu_022C7D70:
0x022C7E64 = wwr_jeu_022C7E64:
0x022D95D8 = wwr_jeu_022D95D8:
0x022E0A70 = wwr_jeu_022E0A70:
0x023198B4 = wwr_jeu_023198B4:
0x02372F78 = wwr_jeu_02372F78:
0x02474F10 = wwr_jeu_02474F10:
0x0247A5CC = wwr_jeu_0247A5CC:
0x024B22F8 = wwr_jeu_024B22F8:
0x024B2300 = wwr_jeu_024B2300:
0x024B27B4 = wwr_jeu_024B27B4:
0x025200D4 = wwr_jeu_025200D4:
0x02520A84 = wwr_jeu_02520A84:
0x025252DC = wwr_jeu_025252DC:
0x025B8B94 = wwr_jeu_025B8B94:
0x025B9834 = wwr_jeu_025B9834:
; [cave]
wwr7_ouvert_028F8E1C:    ; isEventBit_wrapper
    .int 0x9421FFF0              ; stwu r1, -16(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010014              ; stw r0, 20(r1)
    .int 0x7C641B78              ; mr r4, r3
    .int 0x3C601020              ; lis r3, 0x1020
    .int 0x806384DC              ; lwz r3, -31524(r3)
    .int 0x38630644              ; addi r3, r3, 1604
    bl wwr_jeu_025B8B94  ; 4BCBFD5D
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
wwr7_ouvert_028FA5D8:    ; reset_makar_position_to_start_of_dungeon
    .int 0x9421FFF0              ; stwu r1, -16(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010014              ; stw r0, 20(r1)
    bl wwr_jeu_025200D4  ; 4BC25AF1
    .int 0xA903513C              ; lha r8, 20796(r3)
    lis r9, wwr7_ouvert_028FA664@ha ; 3D200290
    addi r9, r9, wwr7_ouvert_028FA664@l ; 3929A664
    .int 0x38000007              ; li r0, 7
    .int 0x7C0903A6             
    .int 0x88090000              ; lbz r0, 0(r9)
    .int 0x7C004000              ; cmpw r0, r8
    .int 0x41820010              ; beq 0x028FA614
    .int 0x39290010              ; addi r9, r9, 16
    .int 0x4200FFF0             
    b wwr7_ouvert_028FA64C       ; 4800003C
    .int 0x81090004              ; lwz r8, 4(r9)
    .int 0x91050000              ; stw r8, 0(r5)
    .int 0x81090008              ; lwz r8, 8(r9)
    .int 0x91050004              ; stw r8, 4(r5)
    .int 0x8109000C              ; lwz r8, 12(r9)
    .int 0x91050008              ; stw r8, 8(r5)
    .int 0xA9090002              ; lha r8, 2(r9)
    .int 0xB105000C              ; sth r8, 12(r5)
    .int 0x7D064378              ; mr r6, r8
    .int 0x7D194378              ; mr r25, r8
    .int 0x89090001              ; lbz r8, 1(r9)
    .int 0x9905000E              ; stb r8, 14(r5)
    .int 0x7D074378              ; mr r7, r8
    .int 0x7D1A4378              ; mr r26, r8
wwr7_ouvert_028FA64C:
    .int 0x386A1148              ; addi r3, r10, 4424
    bl wwr_jeu_025B9834  ; 4BCBF1E5
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
wwr7_ouvert_028FA664:    ; makar_possible_wt_spawn_positions
    .int 0x0F0F94A0             
    .int 0xC5643052             
    .int 0x44C2B571             
    .int 0x464ECCCD             
    .int 0x16004000             
    .int 0xC58322A4             
    .int 0x443CA127             
    .int 0x45E8C400             
    .int 0x1702B000             
    .int 0x442706D9             
    .int 0x44C1C000             
    .int 0x450FAC00             
    .int 0x180CC000             
    .int 0x465DEC66             
    .int 0xC59E33EC             
    .int 0x460BD033             
    .int 0x45034000             
    .int 0xC5819533             
    .int 0x44898000             
    .int 0x423F851F             
    .int 0x14020000             
    .int 0xC2DE947B              ; lfs f22, -27525(r30)
    .int 0xC5610000             
    .int 0xC4DA747B             
    .int 0x460C0000             
    .int 0x465C5000             
    .int 0xC59E33EC             
    .int 0x46115000             
wwr7_ouvert_028FA6D4:    ; reset_medli_position_to_start_of_dungeon
    .int 0x9421FFF0              ; stwu r1, -16(r1)
    .int 0x7C0802A6              ; mflr r0
    .int 0x90010014              ; stw r0, 20(r1)
    bl wwr_jeu_025200D4  ; 4BC259F5
    .int 0xA903513C              ; lha r8, 20796(r3)
    lis r9, wwr7_ouvert_028FA760@ha ; 3D200290
    addi r9, r9, wwr7_ouvert_028FA760@l ; 3929A760
    .int 0x38000007              ; li r0, 7
    .int 0x7C0903A6             
    .int 0x88090000              ; lbz r0, 0(r9)
    .int 0x7C004000              ; cmpw r0, r8
    .int 0x41820010              ; beq 0x028FA710
    .int 0x39290010              ; addi r9, r9, 16
    .int 0x4200FFF0             
    b wwr7_ouvert_028FA748       ; 4800003C
    .int 0x81090004              ; lwz r8, 4(r9)
    .int 0x91050000              ; stw r8, 0(r5)
    .int 0x81090008              ; lwz r8, 8(r9)
    .int 0x91050004              ; stw r8, 4(r5)
    .int 0x8109000C              ; lwz r8, 12(r9)
    .int 0x91050008              ; stw r8, 8(r5)
    .int 0xA9090002              ; lha r8, 2(r9)
    .int 0xB105000C              ; sth r8, 12(r5)
    .int 0x7D064378              ; mr r6, r8
    .int 0x7D1A4378              ; mr r26, r8
    .int 0x89090001              ; lbz r8, 1(r9)
    .int 0x9905000E              ; stb r8, 14(r5)
    .int 0x7D074378              ; mr r7, r8
    .int 0x7D1C4378              ; mr r28, r8
wwr7_ouvert_028FA748:
    .int 0x386A1148              ; addi r3, r10, 4424
    bl wwr_jeu_025B9834  ; 4BCBF0E9
    .int 0x80010014              ; lwz r0, 20(r1)
    .int 0x7C0803A6              ; mtlr r0
    .int 0x38210010              ; addi r1, r1, 16
    .int 0x4E800020              ; blr
wwr7_ouvert_028FA760:    ; medli_possible_et_spawn_positions
    .int 0x00008000             
    .int 0xC5E179AE             
    .int 0xC3480000              ; lfs f26, 0(r8)
    .int 0x45A45652             
    .int 0x1602E000             
    .int 0xC4FBA385             
    .int 0x43480000             
    .int 0xC49DDF0A             
    .int 0x17068000             
    .int 0x45947000             
    .int 0x43AF0000             
    .int 0xC50CB0F6             
    .int 0x180FE000             
    .int 0xC5143614             
    .int 0xC4FA0000             
    .int 0x46045E29             
    .int 0x45010000             
    .int 0xC5FA5000             
    .int 0x447A0000             
    .int 0xC4BC9E14             
    .int 0x09078000             
    .int 0x45C4E87B             
    .int 0x44AF0000             
    .int 0x451334A4             
    .int 0x110F8000             
    .int 0xC5B4A000             
    .int 0xC50FC000             
    .int 0x46179000             
wwr7_ouvert_028FA810:    ; tag_photo_check_lenzo_flag_order
    .int 0x38601701              ; li r3, 5889
    bl wwr7_ouvert_028F8E1C      ; 4BFFE609
    .int 0x2C030000              ; cmpwi r3, 0
    .int 0x41820014              ; beq 0x028FA830
    .int 0x3D801020              ; lis r12, 0x1020
    .int 0x818C84DC              ; lwz r12, -31524(r12)
    .int 0x38801601              ; li r4, 5633
    b wwr_jeu_024B22F8   ; 4BBB7ACC
    b wwr_jeu_024B2300   ; 4BBB7AD0
; [sites]
0x02026D3C = b wwr_jeu_02026D60   ; twwhd avant=40820024 apres=48000024
0x02190EF4 = b wwr_jeu_02190F0C   ; twwhd avant=40820018 apres=48000018
0x0219264C = ori r0, r0, 0                ; twwhd avant=41820184 apres=60000000
0x021A4B4C = beq wwr_jeu_021A4C28 ; twwhd avant=418200AC apres=418200DC
0x021B0BC0 = li r3, 3                     ; twwhd avant=386C00D4 apres=38600003
0x021B0BC4 = bl wwr_jeu_02520A84  ; twwhd avant=484071CD apres=4836FEC1
0x021B51CC = li r3, 3                     ; twwhd avant=386A0644 apres=38600003
0x021B51D0 = bl wwr_jeu_02520A84  ; twwhd avant=484039C5 apres=4836B8B5
0x021CA1A8 = b wwr_jeu_021CA210   ; twwhd avant=7C852378 apres=48000068
0x021CD230 = b wwr_jeu_021CD298   ; twwhd avant=7C852378 apres=48000068
0x021F3F1C = li r3, 1                     ; twwhd avant=483C3E75 apres=38600001
0x021F3FB8 = li r3, 1                     ; twwhd avant=483C3DD9 apres=38600001
0x021F4064 = li r3, 1                     ; twwhd avant=483C3D2D apres=38600001
0x021F40FC = li r3, 1                     ; twwhd avant=483C3C95 apres=38600001
0x021F8BE0 = ori r0, r0, 0                ; twwhd avant=41820058 apres=60000000
0x021FCDE4 = ori r0, r0, 0                ; twwhd avant=40820010 apres=60000000
0x02208D34 = li r3, 1                     ; twwhd avant=48344D1D apres=38600001
0x022099A0 = li r3, 1                     ; twwhd avant=483440B1 apres=38600001
0x02209AF4 = li r3, 1                     ; twwhd avant=48343F5D apres=38600001
0x02209C48 = li r3, 1                     ; twwhd avant=48343E09 apres=38600001
0x02209C70 = li r3, 1                     ; twwhd avant=48343DE1 apres=38600001
0x02209F88 = li r3, 1                     ; twwhd avant=48343AC9 apres=38600001
0x02209FCC = li r3, 1                     ; twwhd avant=48343A85 apres=38600001
0x0220B5CC = li r3, 1                     ; twwhd avant=48342485 apres=38600001
0x0221F8D8 = b wwr_jeu_0221F8EC   ; twwhd avant=41820014 apres=48000014
0x0221FAC4 = ori r0, r0, 0                ; twwhd avant=40820044 apres=60000000
0x0221FAE8 = bl wwr7_ouvert_028FA5D8      ; twwhd avant=48399D4D apres=486DAAF1
0x02227DA4 = li r31, 0                    ; twwhd avant=553FD97F apres=3BE00000
0x02227DA8 = b wwr_jeu_02227DE0   ; twwhd avant=41820038 apres=48000038
0x0222C6D8 = li r4, 14656                 ; twwhd avant=38801D40 apres=38803940
0x0222C6E8 = cmpwi r3, 1                  ; twwhd avant=2C030000 apres=2C030001
0x0222C9BC = b wwr_jeu_0222CA1C   ; twwhd avant=4180000C apres=48000060
0x02286314 = b wwr_jeu_022863E8   ; twwhd avant=418200D4 apres=480000D4
0x02286A98 = ori r0, r0, 0                ; twwhd avant=40820044 apres=60000000
0x02286ABC = bl wwr7_ouvert_028FA6D4      ; twwhd avant=48332D79 apres=48673C19
0x022C7A4C = b wwr_jeu_022C7A70   ; twwhd avant=40820024 apres=48000024
0x022C7D4C = b wwr_jeu_022C7D70   ; twwhd avant=40820024 apres=48000024
0x022C7E40 = b wwr_jeu_022C7E64   ; twwhd avant=40820024 apres=48000024
0x022D9520 = b wwr_jeu_022D95D8   ; twwhd avant=408200B8 apres=480000B8
0x022DEF78 = ori r0, r0, 0                ; twwhd avant=41820020 apres=60000000
0x022E0A5C = b wwr_jeu_022E0A70   ; twwhd avant=40820014 apres=48000014
0x023149EC = ori r0, r0, 0                ; twwhd avant=418201D8 apres=60000000
0x023164C8 = li r3, 0                     ; twwhd avant=5403D97E apres=38600000
0x0231987C = b wwr_jeu_023198B4   ; twwhd avant=8003042C apres=48000038
0x02339554 = li r3, 0                     ; twwhd avant=4827E83D apres=38600000
0x023465DC = ori r0, r0, 0                ; twwhd avant=418200D8 apres=60000000
0x02372F74 = ori r0, r0, 0                ; twwhd avant=418200EC apres=60000000
0x0237305C = b wwr_jeu_02372F78   ; twwhd avant=4082FF1C apres=4BFFFF1C
0x02374648 = ori r0, r0, 0                ; twwhd avant=418200B4 apres=60000000
0x023746CC = ori r0, r0, 0                ; twwhd avant=41820030 apres=60000000
0x02386BA0 = li r3, 3                     ; twwhd avant=386C00D4 apres=38600003
0x02386BA4 = bl wwr_jeu_02520A84  ; twwhd avant=482311ED apres=48199EE1
0x02387500 = li r3, 3                     ; twwhd avant=386B0644 apres=38600003
0x02387504 = bl wwr_jeu_02520A84  ; twwhd avant=48231691 apres=48199581
0x023A4A98 = ori r0, r0, 0                ; twwhd avant=41820010 apres=60000000
0x024731E8 = li r3, 1                     ; twwhd avant=48142B59 apres=38600001
0x0247348C = li r3, 1                     ; twwhd avant=481428B5 apres=38600001
0x02474B58 = b wwr_jeu_02474F10   ; twwhd avant=41820058 apres=480003B8
0x02474B7C = b wwr_jeu_02474F10   ; twwhd avant=41820034 apres=48000394
0x02474B9C = b wwr_jeu_02474F10   ; twwhd avant=41820374 apres=48000374
0x0247A574 = b wwr_jeu_0247A5CC   ; twwhd avant=40820058 apres=48000058
0x024B22F4 = b wwr7_ouvert_028FA810       ; twwhd avant=38801601 apres=4844851C
0x024B2798 = b wwr_jeu_024B27B4   ; twwhd avant=4182001C apres=4800001C
0x024D4F1C = ori r0, r0, 0                ; twwhd avant=480E3C4D apres=60000000
0x024EA7E8 = li r3, 3                     ; twwhd avant=386C00D4 apres=38600003
0x024EA7EC = bl wwr_jeu_02520A84  ; twwhd avant=480CD5A5 apres=48036299
0x025221F0 = blr                          ; twwhd avant=7C0802A6 apres=4E800020
0x025224A4 = blr                          ; twwhd avant=9421FF60 apres=4E800020
0x02522C60 = blr                          ; twwhd avant=9421FFA0 apres=4E800020
0x025255F4 = b wwr_jeu_025252DC   ; twwhd avant=80BC84DC apres=4BFFFCE8
0x02551220 = li r3, 1                     ; twwhd avant=4BFFC831 apres=38600001
0x02551244 = li r3, 1                     ; twwhd avant=4BFFC80D apres=38600001
0x0255B46C = li r3, 1                     ; twwhd avant=4805A8D5 apres=38600001
0x025B9B98 = ori r0, r0, 0                ; twwhd avant=4BFFEF5D apres=60000000
0x025B9BB0 = ori r0, r0, 0                ; twwhd avant=4BFFF05D apres=60000000
0x025B9C28 = ori r0, r0, 0                ; twwhd avant=9B8C0064 apres=60000000
0x025B9C48 = ori r0, r0, 0                ; twwhd avant=4BFFC085 apres=60000000
0x025B9C5C = ori r0, r0, 0                ; twwhd avant=4BFFC071 apres=60000000
0x02677D3C = blr                          ; twwhd avant=7C0802A6 apres=4E800020
0x02678B74 = blr                          ; twwhd avant=7C0802A6 apres=4E800020
0x026790B0 = li r3, 1                     ; twwhd avant=4BF3CBD1 apres=38600001
