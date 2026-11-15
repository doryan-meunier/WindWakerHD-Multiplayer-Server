# Serveur Wind Waker Coop (wwmp) dans un conteneur. Aucun fichier du jeu, aucune dépendance.
# Construit depuis la RACINE du dépôt (docker-compose.yml s'en charge) :
#   docker build -f serveur_dedie/docker/Dockerfile -t wwmp-serveur .
# TOUT est dans le volume /donnees : config.json (réglée depuis le lanceur, « Administrer le serveur »), admin.txt
# (compte admin), mot_de_passe.txt, mondes/, cles.json, spoilers, mises_a_jour/. Pas à pas : docs/hotes/JOUER_PAR_INTERNET.md.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    HOME=/donnees \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1

# L'UTILISATEUR qui fait tourner le serveur se choisit dans docker-compose.yml (« user: », 10001:10001 par défaut,
# réglable par WWMP_UID / WWMP_GID dans .env) : l'image n'en crée aucun. /donnees appartient à 10001 pour qu'un volume
# neuf soit écrivable par l'utilisateur par défaut ; avec un autre UID, donner le volume à cet UID (README).
RUN mkdir -p /donnees /app \
    && chown 10001:10001 /donnees \
    && chmod 700 /donnees

WORKDIR /app
# le serveur et ce qu'il importe (tests/test_organisation.py le vérifie) : commun, jeu, synchro, rando, serveur
COPY tools/wwmp_server.py /app/
COPY tools/commun /app/commun
COPY tools/jeu /app/jeu
COPY tools/synchro /app/synchro
COPY tools/rando /app/rando
COPY tools/serveur /app/serveur

VOLUME ["/donnees"]
EXPOSE 7777/tcp

# le port répond-il ? (simple connexion TCP, sans se présenter)
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, socket; socket.create_connection(('127.0.0.1', int(os.environ.get('PORT', '7777'))), 3).close()"]

# SIGTERM (docker stop) : le serveur prévient les joueurs puis s'arrête
STOPSIGNAL SIGTERM
# --config : la configuration de la partie vit dans le volume et se règle à distance (serveur/admin.py) ; un redémarrage
# demandé par l'administrateur arrête proprement le serveur, « restart: unless-stopped » le relance
ENTRYPOINT ["python", "-u", "/app/wwmp_server.py", "--donnees", "/donnees", "--config", "/donnees/config.json"]
CMD ["--port", "7777"]
