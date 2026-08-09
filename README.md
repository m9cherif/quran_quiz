# Quran Competition Server

Backend FastAPI + Supabase PostgreSQL pour organiser des **compétitions en temps réel
avec questions à durée limitée** (QCM, Vrai/Faux, texte, nombre, audio).

Les participants utilisent simplement leur navigateur (téléphone, tablette, PC) via WebSocket
et HTTP. **Le serveur est la seule autorité sur le temps et le calcul des points.**

## 1. Installation

```bash
python -m pip install -r requirements.txt
```

Python 3.11+ requis (développé sous 3.12, fonctionne sous Windows et Linux/Render).

## 2. Dépendances

- `fastapi` — API REST + WebSocket + Swagger
- `uvicorn[standard]` — serveur ASGI
- `supabase` — SDK Python Supabase (PostgREST + PostgreSQL)
- `pydantic` — validation des modèles
- `python-dotenv` — chargement du `.env`
- `httpx` / `pytest` — tests

## 3. Configuration `.env`

```bash
cp .env.example .env
```

| Variable | Obligatoire | Rôle |
|---|---|---|
| `SUPABASE_URL` | oui | URL du projet Supabase |
| `SUPABASE_KEY` | non (réservé futur) | clé anon/publishable, jamais exposée par ce serveur |
| `SUPABASE_SERVICE_ROLE_KEY` | oui | utilisée par le serveur uniquement (jamais envoyée au navigateur) |
| `ADMIN_API_KEY` | oui | clé des routes `/api/admin/*` (`Authorization: Bearer <clé>`) |
| `CORS_ORIGINS` | non | origines autorisées séparées par des virgules |

> **Générer une clé admin :** `python -c "import secrets; print(secrets.token_urlsafe(48))"`

Le serveur refuse de démarrer si `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` ou
`ADMIN_API_KEY` manquent (les noms seulement sont affichés, jamais les valeurs).
`.env` est ignoré par Git (voir `.gitignore`).

## 4. Création de la base Supabase

1. Créez un projet sur [supabase.com](https://supabase.com).
2. Ouvrez **SQL Editor > New query**, collez le contenu de `supabase/schema.sql`, exécutez.
3. Récupérez dans **Project Settings > API** : `Project URL`, `anon public key`,
   `service_role key`.

Le schéma crée : `competitions`, `participants`, `questions`, `choices`, `answers`,
avec index, clés étrangères (cascade), et la contrainte `UNIQUE(participant_id, question_id)`
qui **bloque les doubles réponses au niveau base de données** (le serveur vérifie aussi).

## 5. Lancement du serveur

```bash
uvicorn main:app --reload
```

Sur Windows, si `uvicorn` n'est pas dans le PATH : `python -m uvicorn main:app --reload`.
En production (Render), le port est fourni par l'environnement — voir `Procfile` :
`uvicorn main:app --host 0.0.0.0 --port $PORT`.

## 6. Interface web (navigateur, mobile/tablette/PC)

L'application UI est servie par le même serveur (Jinja2 + JS vanilla, aucun build Node) :

| URL | Usage |
|---|---|
| `/` | Accueil : choisir « Je participe » ou « Administration » |
| `/join` | Le participant entre le **code** et son **nom** → rejoint la salle |
| `/room/{competition_id}` | Salle du participant : attente → question (compte à rebours) → feedback → classement |
| `/admin` | Console organisateur : créations, questions, lancement, pause/reprise/fin, participants, classement |

- Interface **mobile-first** et responsive (cartes tactiles, thème sombre vert/or).
- Le compte à rebours affiché est dérivé des timestamps serveur ; l'horloge navigateur
  ne valide rien.
- La clé admin n'est saisie que dans la console `/admin` (stockée dans le navigateur localement).
- Le token participant est stocké en `localStorage` ; le rechargement ou la perte de
  connexion WebSocket se reconnecte automatiquement.

Tests d'API : `/docs` (Swagger), tous les modèles Pydantic y sont décrits.

### Flux type

1. `POST /api/admin/competitions` — créez une compétition (code ex. `QURAN2026`).
2. `POST /api/admin/competitions/{id}/questions` — ajoutez des questions.
3. `POST /api/admin/questions/{id}/choices` — pour les QCM/Vrai-Faux, en indiquant `is_correct`.
4. Les participants : `POST /api/competitions/join` avec le code.
5. `POST /api/admin/competitions/{id}/start` — la salle d'attente passe à `running`.
6. `POST /api/admin/competitions/{id}/questions/{qid}/start` — lance une question.
7. Les participants répondent via `POST /api/competitions/{id}/answers`.
8. Pause : `POST .../pause`, reprise : `POST .../resume`.
9. Fin : `POST /api/admin/competitions/{id}/finish`.

## 7. WebSocket

`ws://127.0.0.1:8000/ws/competition/{competition_id}`
En production Render : `wss://<votre-app>.onrender.com/ws/competition/{competition_id}`

1. Le serveur envoie `identify_required`.
2. Envoyez `{"type":"identify","role":"participant"|"admin","token":"..."}`
   (participant : token opaque reçu au join ; admin : clé admin).
3. Vous recevez ensuite les événements JSON :

```
competition_state      participant_joined   participant_left
question_started       question_ended       answer_received
leaderboard_updated    competition_started  competition_paused
competition_resumed    competition_finished error
```

Heartbeat : le serveur envoie `{"type":"ping"}` toutes les 30 s — tout message reçu
(par ex. `{"type":"pong"}`) maintient la connexion vivante ; au-delà de 90 s de silence,
la connexion est purgée.

> Le message `question_started` inclut les choix **sans** `is_correct`. La bonne réponse
> n'est jamais diffusée.

## 8. Déploiement sur Render

Le dépôt contient déjà tout le nécessaire :
- `render.yaml` — Blueprint Render (runtime Python 3.12, health check `/health`)
- `Procfile` — commande de démarrage avec `$PORT`
- `.python-version` — version Python

### 8.1 Prérequis

- Dépôt GitHub poussé (fait : `m9cherif/quran_quiz`).
- Projet Supabase avec le schéma `supabase/schema.sql` appliqué (section 4).

### 8.2 Créer le service (recommandé : Blueprint)

1. Allez sur https://dashboard.render.com → bouton **New** → **Blueprint**.
2. Connectez le dépôt GitHub `m9cherif/quran_quiz` (autorisez Render sur GitHub si demandé).
3. Render détecte `render.yaml` et propose le service **quran-quiz-server**.
4. Cliquez **Apply**. Render lance une première fois le build **sans** les secrets
   (variables `sync: false`) — le démarrage échouera tant qu'elles sont vides, c'est normal.
5. Ouvrez le service créé → onglet **Environment** → ajoutez la valeur de chaque variable
   listée à la section 8.4 → **Save Changes** (déclenche un redéploiement automatique).
6. Attendez que l'état du service passe à **Live** (verts). Ouvrez les **Logs** pour suivre.

### 8.3 Alternative : créer le service manuellement

1. **New → Web Service** → connectez le repo GitHub `m9cherif/quran_quiz`.
2. Champs à renseigner :
   - **Name** : `quran-quiz-server`
   - **Runtime** : `Python 3`
   - **Build Command** : `pip install -r requirements.txt`
   - **Start Command** : `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type** : Free (test) ou Starter (recommandé pour le temps réel)
   - **Health Check Path** : `/health`
3. **Advanced → Environment Variables** : ajoutez celles de la section 8.4.
4. **Create Web Service**, puis attendez le déploiement.

### 8.4 Variables d'environnement à définir sur Render

| Variable | Valeur |
|---|---|
| `SUPABASE_URL` | `https://<votre-projet>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | `service_role` key du projet (Settings > API) — **jamais** envoyée au navigateur |
| `SUPABASE_KEY` | `anon public key` (optionnelle) |
| `ADMIN_API_KEY` | clé aléatoire longue — générez-la, ne mettez jamais le placeholder |
| `CORS_ORIGINS` | l'origine de votre interface : `https://<votre-app>.onrender.com` (+ éventuel site statique) |
| `LOG_LEVEL` | `INFO` |

Générer une clé admin : `python -c "import secrets; print(secrets.token_urlsafe(48))"`

### 8.5 Vérifications après déploiement

```bash
curl https://<votre-app>.onrender.com/health          # → {"success":true,...}
curl https://<votre-app>.onrender.com/health/database # 200 si Supabase joignable, 503 sinon
curl https://<votre-app>.onrender.com/docs            # Swagger
```

Tester le WebSocket (connexion + `identify_required`) :
```javascript
const ws = new WebSocket("wss://<votre-app>.onrender.com/ws/competition/test");
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

### 8.6 Points critiques pour Render

- **Instance unique obligatoire** : l'état du jeu (question active, chronomètre, cache du
  classement) vit en mémoire. Ne passez **jamais** à plusieurs instances.
- **Plan Free** : l'instance s'endort après ~15 min sans trafic → **cold start** lent au
  premier appel et **WebSocket indisponibles** pendant le sommeil. Pour une vraie
  compétition temps réel, utilisez un plan **Starter** (ou supérieur) afin de garder les
  connexions WebSocket permanentes.
- Le heartbeat serveur (ping toutes les 30 s) maintient les WebSocket actives côté proxy Render.
- Aucun secret n'apparaît dans les **Logs** Render (le code ne logue jamais les clés).

## 9. Tests

```bash
python -m pytest -q
```

- **Tests unitaires** (sans base) : enveloppes de réponse, erreurs, validation,
  scoring, rate limiting, garde admin, payload `question_started` sans réponse correcte,
  fenêtre de temps (actif/pause/expiré).
- **Tests d'intégration** (avec une vraie base Supabase dans `.env`) : création
  compétition/question/choix, join, salle d'attente, start, réponses correcte/mauvaise,
  réponse tardive, double réponse, pause/reprise/fin, classement, WebSocket.
  Ils se **skippent automatiquement** tant que les vraies clés ne sont pas renseignées.

## 10. Sécurité

- Clés Supabase et clé admin : uniquement dans `.env` (ou variables Render), jamais dans
  le code ni les logs.
- Routes admin : `Authorization: Bearer <ADMIN_API_KEY>` comparée en temps constant ;
  architecture prête à basculer vers Supabase Auth.
- Session participant : token opaque (~43 octets aléatoires) stocké en base ;
  le `display_name` n'est jamais une identité.
- Le **serveur** calcule `started_at` + `duration_seconds` → `ends_at` et **refuse**
  toute réponse après la fin, quel que soit l'horloge du navigateur.
- La bonne réponse (`is_correct`, `correct_answer_text`) reste côté serveur.
- Le score vient toujours du serveur ; le client ne peut pas influencer le classement.
- Validation Pydantic complète, limite de corps à 64 Ko, rate limiting simple en
  mémoire, enveloppes d'erreur JSON uniformes (aucune trace Python exposée).
- CORS configurable via `CORS_ORIGINS` (pas de `*` par défaut).

## 11. Déploiement futur

- Migrer l'état en mémoire (questions actives, classement) vers SQL/realtime Supabase
  pour le multi-instance, ou conserver une seule instance derrière un reverse proxy.
- Remplacer la clé admin par Supabase Auth (JWT) : seule `app/security.py` change.
- RLS : recommandé (voir commentaire en fin de `supabase/schema.sql`).
- Bonus de rapidité : déjà calculé côté serveur quand `speed_bonus_enabled` est vrai
  sur la compétition (jusqu'à +50 % des points selon la rapidité), champ `bonus_points`
  prévu dans `answers`.

## Routes

| Méthode | Route | Accès |
|---|---|---|
| GET | `/health`, `/health/database` | public |
| POST | `/api/competitions/join` | public (limit 10/min/IP) |
| GET | `/api/competitions/{id}/waitroom` | participant |
| POST | `/api/competitions/{id}/answers` | participant (limit 30/min/IP) |
| GET | `/api/competitions/{id}/leaderboard` | participant ou admin |
| WS | `/ws/competition/{id}` | avec identification |
| POST/GET | `/api/admin/competitions...` | admin |
| POST/PUT/DELETE | `/api/admin/questions/...`, `/api/admin/choices/...` | admin |

Code d'erreur : `COMPETITION_NOT_FOUND`, `COMPETITION_NOT_RUNNING`,
`COMPETITION_NOT_ACCEPTING_PARTICIPANTS`, `QUESTION_NOT_ACTIVE`, `QUESTION_EXPIRED`,
`ALREADY_ANSWERED`, `INVALID_ANSWER`, `NOT_AUTHORIZED`, `PARTICIPANT_NOT_FOUND`,
`DATABASE_ERROR`, `INVALID_REQUEST`, `RATE_LIMIT_EXCEEDED`.