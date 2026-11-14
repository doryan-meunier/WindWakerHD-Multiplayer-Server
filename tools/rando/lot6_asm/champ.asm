; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,
; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.
; Converti par tools/wwmp/rando_lot6.py (--porter) avec tools/wwmp/rando_port_twwhd.py.
; FICHIER GENERE : ne pas le modifier a la main. Utilise SEULEMENT par le pack WWMP_Rando genere.

; [symboles]
0x0217E18C = wwr_jeu_0217E18C:
0x0217F1D0 = wwr_jeu_0217F1D0:
0x0217F1E8 = wwr_jeu_0217F1E8:
0x0217F1FC = wwr_jeu_0217F1FC:
0x021815E8 = wwr_jeu_021815E8:
0x02182D84 = wwr_jeu_02182D84:
0x02182DD8 = wwr_jeu_02182DD8:
0x02182E5C = wwr_jeu_02182E5C:
; [cave]
; [sites]
0x0217DE64 = bgt wwr_jeu_0217E18C   ; twwhd avant=41810604 apres=41810328
0x0217DEE0 = b wwr_jeu_0217E18C     ; twwhd avant=280B0034 apres=480002AC
0x0217DF2C = b wwr_jeu_0217E18C     ; twwhd avant=88BF0326 apres=48000260
0x0217DF84 = b wwr_jeu_0217E18C     ; twwhd avant=88BF0326 apres=48000208
0x0217F1B4 = lbz r3, 1870(r3)             ; twwhd avant=8803074E apres=8863074E
0x0217F1B8 = cmplwi r3, 0x38              ; twwhd avant=28000038 apres=28030038
0x0217F1BC = beq wwr_jeu_0217F1E8   ; twwhd avant=4182002C apres=4182002C
0x0217F1C0 = addi r30, r3, -69            ; twwhd avant=2800003B apres=3BC3FFBB
0x0217F1C4 = cmplwi r30, 0x6              ; twwhd avant=41820018 apres=281E0006
0x0217F1C8 = ble wwr_jeu_0217F1D0   ; twwhd avant=2800003D apres=40810008
0x0217F1CC = cmplwi r3, 0x1F              ; twwhd avant=41820028 apres=2803001F
0x0217F1D8 = ble wwr_jeu_0217F1FC   ; twwhd avant=4E800020 apres=40810024
0x02182D70 = cmpwi r0, 32                 ; twwhd avant=28000035 apres=2C000020
0x02182D74 = mr r3, r26                   ; twwhd avant=418000E0 apres=7F43D378
0x02182D78 = blt wwr_jeu_02182D84   ; twwhd avant=28000036 apres=4180000C
0x02182D7C = bl wwr_jeu_021815E8    ; twwhd avant=4081005C apres=4BFFE86D
0x02182D80 = b wwr_jeu_02182E5C     ; twwhd avant=7F43D378 apres=480000DC
0x02182DA8 = blt wwr_jeu_02182DD8   ; twwhd avant=418000AC apres=41800030
0x02182DB8 = bl wwr_jeu_021815E8    ; twwhd avant=4BFFE02D apres=4BFFE831
