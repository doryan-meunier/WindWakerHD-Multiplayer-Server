# Joueur robot de Wind Waker Coop, EXPÉRIMENTAL (docs/hotes/ESSAYER_LE_ROBOT.md, docs/etudes/joueur_robot.md).
#
# Une image À PART de celle du serveur : celle du serveur reste sans aucun fichier du jeu. Celle-ci ne contient que
# notre code (tools/), Python, Mesa lavapipe (Vulkan sur le CPU : pas de carte graphique), Xvfb (écran virtuel) et
# le chargeur Vulkan. JAMAIS le jeu : le portage Linux, préparé par le propriétaire du serveur depuis SON dump, est
# monté en volume (/portage) au lancement. Ne publie jamais une image qui aurait reçu un fichier du jeu
# (pas de « docker commit » d'un conteneur lancé).
#
# Jamais essayé : le jeu n'a encore jamais tourné sous Linux (ni chez le portage, ni chez nous). Sous Windows avec une
# carte graphique, préférer --robot-portage du serveur (rien à construire).
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 mesa-vulkan-drivers libvulkan1 xvfb xauth \
      libgl1 libx11-6 libxext6 libxrandr2 libxcursor1 libxi6 libxss1 libasound2 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /wwmp
COPY tools/ /wwmp/tools/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json

# xvfb-run : un écran virtuel pour SDL (le portage crée ses fenêtres, cachées) ; le robot lance le portage caché
# puis le client --robot (tools/client/robot.py)
ENTRYPOINT ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24", "python3", "/wwmp/tools/client/robot.py"]
CMD ["--hote", "wwmp-serveur", "--port", "7777", "--portage", "/portage", "--dossier", "/robot"]
