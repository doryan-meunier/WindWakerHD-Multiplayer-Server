; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,
; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.
; Converti par tools/wwmp/rando_lot7.py (--porter) avec tools/wwmp/rando_port_twwhd.py.
; FICHIER GENERE : ne pas le modifier a la main. Utilise SEULEMENT par le pack WWMP_Rando genere.

; [symboles]
0x02439E08 = wwr_jeu_02439E08:
; [cave]
; [sites]
0x02482764 = ori r0, r0, 0                ; twwhd avant=4081002C apres=60000000
0x0247DCAC = ori r0, r0, 0                ; twwhd avant=48163D95 apres=60000000
0x02439DE4 = b wwr_jeu_02439E08 ; twwhd avant=40820024 apres=48000024
0x0243AD3C = ori r0, r0, 0                ; twwhd avant=418200A8 apres=60000000
0x022EFB2C = li r0, 1                     ; twwhd avant=38000002 apres=38000001
0x022ECC1C = ori r0, r0, 0                ; twwhd avant=980C0243 apres=60000000
