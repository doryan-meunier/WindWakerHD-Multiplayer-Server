; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,
; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.
; Converti par tools/wwmp/rando_lot6.py (--porter) avec tools/wwmp/rando_port_twwhd.py.
; FICHIER GENERE : ne pas le modifier a la main. Utilise SEULEMENT par le pack WWMP_Rando genere.

; [symboles]
; [cave]
; [sites]
0x025D8A70 = ori r0, r0, 0                ; twwhd avant=38800008 apres=60000000
0x0212456C = lbz r4, 176(r12)             ; twwhd avant=38800000 apres=888C00B0
0x020D0AE4 = lbz r4, 178(r31)             ; twwhd avant=38800001 apres=889F00B2
0x020D0AF0 = li r8, 1                     ; twwhd avant=7C882378 apres=39000001
0x02124578 = li r7, 0                     ; twwhd avant=7C872378 apres=38E00000
0x02124580 = li r8, 0                     ; twwhd avant=7C882378 apres=39000000
0x021031F0 = li r6, 0                     ; twwhd avant=7C862378 apres=38C00000
0x0254DCA8 = ori r0, r0, 0                ; twwhd avant=4BFD2E61 apres=60000000
0x0254DCD8 = ori r0, r0, 0                ; twwhd avant=4806B3C1 apres=60000000
0x025D8A84 = li r5, 21                    ; twwhd avant=38A0FFFF apres=38A00015
