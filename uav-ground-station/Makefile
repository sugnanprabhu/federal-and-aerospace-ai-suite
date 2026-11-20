# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

.PHONY: help init build _build_if_changed _check_certs up up-vss _check_vss_env down down-vss clean-vss-index delete-vss-volumes logs

ifneq (,$(wildcard .env))
  include .env
  export
endif

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / \
	  {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

init: ## Create .env from template if missing, and auto-detect host IP and GPU/NPU device paths
	@if test -f .env; then \
		echo ".env already exists — skipping (delete it to re-run init)"; \
	else \
		cp .env.example .env; \
		echo "[init] .env ready. Edit VIZ_COMPANION_HOST to point at your UAV companion-compute board's IP."; \
		HOST_IP_DETECTED=$$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($$i=="src") {print $$(i+1); exit}}'); \
		[ -n "$$HOST_IP_DETECTED" ] || HOST_IP_DETECTED=$$(hostname -I 2>/dev/null | awk '{print $$1}'); \
		if [ -z "$$HOST_IP_DETECTED" ]; then \
			HOST_IP_DETECTED=127.0.0.1; \
			echo "⚠  Could not auto-detect host IP — defaulting VIZ_HOST_IP/VIZ_PUBLIC_HOST=127.0.0.1 (edit .env manually if needed)."; \
		else \
			echo "✅ Host IP detected:"; \
			echo "   VIZ_HOST_IP=$$HOST_IP_DETECTED"; \
			echo "   VIZ_PUBLIC_HOST=$$HOST_IP_DETECTED"; \
			echo "   ⚠  Security: the dashboard (8080), mediamtx WebRTC (8889/8189) and VSS"; \
			echo "      Pipeline Manager (3001) ports will be published on this LAN address."; \
			echo "      Set VIZ_HOST_IP=127.0.0.1 and VIZ_PUBLIC_HOST=127.0.0.1 in .env for"; \
			echo "      loopback-only access if this host is not on a trusted, isolated network."; \
		fi; \
		sed -i "s|^VIZ_HOST_IP=.*|VIZ_HOST_IP=$$HOST_IP_DETECTED|" .env; \
		sed -i "s|^VIZ_PUBLIC_HOST=.*|VIZ_PUBLIC_HOST=$$HOST_IP_DETECTED|" .env; \
		GPU_CARD=$$(ls -1 /dev/dri/card* 2>/dev/null | head -1); \
		GPU_RENDER=$$(ls -1 /dev/dri/renderD* 2>/dev/null | head -1); \
		if [ -n "$$GPU_CARD" ] && [ -n "$$GPU_RENDER" ]; then \
			echo "✅ GPU detected: GPU_DEVICE=$$GPU_CARD GPU_RENDER_DEVICE=$$GPU_RENDER"; \
			sed -i "s|^GPU_DEVICE=.*|GPU_DEVICE=$$GPU_CARD|" .env; \
			sed -i "s|^GPU_RENDER_DEVICE=.*|GPU_RENDER_DEVICE=$$GPU_RENDER|" .env; \
		else \
			echo "⚠  No Intel GPU found at /dev/dri/ — leaving GPU_DEVICE=/dev/null (gs-metrics-manager won't report GPU usage)."; \
		fi; \
		NPU_DEV=$$(ls -1 /dev/accel/accel* 2>/dev/null | head -1); \
		if [ -n "$$NPU_DEV" ]; then \
			echo "✅ NPU detected: NPU_DEVICE=$$NPU_DEV"; \
			sed -i "s|^NPU_DEVICE=.*|NPU_DEVICE=$$NPU_DEV|" .env; \
		else \
			echo "⚠  No Intel NPU found at /dev/accel/ — leaving NPU_DEVICE=/dev/null (gs-metrics-manager won't report NPU usage)."; \
		fi; \
		echo ""; \
		echo "[init] Generating VSS overlay credentials (MinIO/Postgres are internal to this"; \
		echo "       stack only, never exposed beyond the docker network."; \
		echo "       see .env, or edit it yourself with your own values before 'make up-vss'):"; \
		sed -i "s|^VSS_MINIO_USER=.*|VSS_MINIO_USER=vss-admin|" .env; \
		sed -i "s|^VSS_MINIO_PASSWORD=.*|VSS_MINIO_PASSWORD=$$(openssl rand -hex 16)|" .env; \
		sed -i "s|^VSS_POSTGRES_USER=.*|VSS_POSTGRES_USER=vss|" .env; \
		sed -i "s|^VSS_POSTGRES_PASSWORD=.*|VSS_POSTGRES_PASSWORD=$$(openssl rand -hex 16)|" .env; \
		echo "   ✅ VSS_MINIO_USER / VSS_MINIO_PASSWORD / VSS_POSTGRES_USER / VSS_POSTGRES_PASSWORD set"; \
		if docker volume ls -q 2>/dev/null | grep -q '_vss-pg-data$$'; then \
			echo "   ⚠  A VSS database from an earlier deployment still exists and keeps its old password,"; \
			echo "      so VSS would fail to start. Run 'make clean-vss-index' to start with an empty"; \
			echo "      index, or put the previous VSS_POSTGRES_PASSWORD back into .env."; \
		fi; \
		echo ""; \
		echo "[init] VSS downloads two Hugging Face models the first time 'make up-vss' runs"; \
		echo "       (by the vss-dataprep/vss-embedding containers), subject to their"; \
		echo "       publishers' license terms:"; \
		echo "         VSS_EMBEDDING_MODEL=CLIP/clip-vit-b-32"; \
		echo "         DATAPREP_DETECTION_MODEL=OpenVINO/yolox_s-int8-ov"; \
		if [ "$(ACCEPT_VSS_MODELS)" = "y" ]; then ANSWER=y; \
		else printf "Pre-fill .env with these default models now? [y/N]: "; read ANSWER || ANSWER=N; fi; \
		case "$$ANSWER" in \
			y|Y|yes|YES) \
				sed -i "s|^VSS_EMBEDDING_MODEL=.*|VSS_EMBEDDING_MODEL=CLIP/clip-vit-b-32|" .env; \
				sed -i "s|^DATAPREP_DETECTION_MODEL=.*|DATAPREP_DETECTION_MODEL=OpenVINO/yolox_s-int8-ov|" .env; \
				echo "   ✅ VSS_EMBEDDING_MODEL / DATAPREP_DETECTION_MODEL set to the defaults above"; \
				;; \
			*) \
				echo "   ⚠  Skipped -- VSS_EMBEDDING_MODEL / DATAPREP_DETECTION_MODEL left blank in .env."; \
				echo "      Before running 'make up-vss', set your own models, either by editing .env"; \
				echo "      directly or with:"; \
				echo "        sed -i 's|^VSS_EMBEDDING_MODEL=.*|VSS_EMBEDDING_MODEL=<org>/<model>|' .env"; \
				echo "        sed -i 's|^DATAPREP_DETECTION_MODEL=.*|DATAPREP_DETECTION_MODEL=OpenVINO/<model>|' .env"; \
				echo "      then run 'make up-vss' -- 'make up' (no VSS) needs no change. _check_vss_env"; \
				echo "      fails fast with a clear message if either is still blank at that point."; \
				;; \
		esac; \
	fi

build: ## Force a rebuild of the viz-backend image (up and up-vss rebuild it only when missing or backend/ changed)
	docker compose -f docker-compose.yml build

BACKEND_IMAGE = uav-ground-station-backend:local

# Builds viz-backend only when the image is missing or a file in backend/ is
# newer than it, so a start with unchanged code never needs the network.
_build_if_changed:
	@created=$$(docker image inspect -f '{{.Created}}' $(BACKEND_IMAGE) 2>/dev/null); \
	if [ -z "$$created" ]; then \
		echo "[build] $(BACKEND_IMAGE) not found -- building"; \
		docker compose -f docker-compose.yml build; \
	else \
		built=$$(date -d "$$created" +%s); \
		newest=$$(find backend -type f -not -path '*/__pycache__/*' -printf '%T@\n' | sort -n | tail -1 | cut -d. -f1); \
		if [ "$$newest" -gt "$$built" ]; then \
			echo "[build] backend/ changed since the image was built -- rebuilding"; \
			docker compose -f docker-compose.yml build; \
		else \
			echo "[build] $(BACKEND_IMAGE) is up to date -- skipping build"; \
		fi; \
	fi

# Host ports that something else on this machine already uses (for example the UAV Mission
# Compute SDK) are moved to the next free one in .env first.
RESOLVE_PORTS = ./scripts/resolve-ports.sh VIZ_PORT:8080 MEDIAMTX_WEBRTC_PORT:8889 MEDIAMTX_WEBRTC_ICE_UDP_PORT+MEDIAMTX_WEBRTC_ICE_TCP_PORT:8189

# Recipes source .env because make exports the values it read at startup, which would otherwise
# override a change made to .env by an earlier prerequisite (for example DEVICE= or a moved port).
up: init _build_if_changed ## Start the ground-station viewer + mediamtx relay sidecar (H.264 Relay mode); builds viz-backend only if missing or changed
	@$(RESOLVE_PORTS)
	@set -a; . ./.env; set +a; \
	docker compose -f docker-compose.yml -f docker-compose-mediamtx.yml up -d --no-build && \
	echo "" && echo "✅ UAV ground station is up. Open the dashboard at: http://$$VIZ_HOST_IP:$${VIZ_PORT:-8080}"

down: ## Stop and remove the ground-station viewer + mediamtx relay sidecar
	docker compose -f docker-compose.yml -f docker-compose-mediamtx.yml down -v

VSS_COMPOSE = docker compose -f docker-compose.yml -f docker-compose-mediamtx.yml -f docker-compose-vss.yml -f docker-compose-nginx.yml

VSS_REQUIRED_VARS = VSS_MINIO_USER VSS_MINIO_PASSWORD VSS_POSTGRES_USER VSS_POSTGRES_PASSWORD VSS_EMBEDDING_MODEL DATAPREP_DETECTION_MODEL

# One message listing everything missing, instead of one compose error per use.
_check_vss_env:
	@missing=""; \
	for v in $(VSS_REQUIRED_VARS); do \
		eval "val=\$$$$v"; \
		[ -n "$$val" ] || missing="$$missing $$v"; \
	done; \
	if [ -n "$$missing" ]; then \
		echo "ERROR: set these in .env before make up-vss (see .env.example):$$missing" >&2; \
		exit 1; \
	fi; \
	if [ $${#VSS_MINIO_USER} -lt 3 ] || [ $${#VSS_MINIO_PASSWORD} -lt 8 ]; then \
		echo "ERROR: MinIO needs VSS_MINIO_USER with at least 3 characters and VSS_MINIO_PASSWORD with at least 8 characters." >&2; \
		exit 1; \
	fi

# make up-vss DEVICE=CPU|GPU|NPU|all -- sets VSS_INGEST_EMBEDDING_DEVICE,
# VSS_DETECTION_DEVICE and VSS_QUERY_EMBEDDING_DEVICE together in .env so you
# don't have to hand-edit three variables (see docs/user-guide/how-to-guides/vss-live-search.md).
# DEVICE=all auto-picks the best hardware `make init` already detected (GPU if
# GPU_DEVICE isn't /dev/null, else NPU if NPU_DEVICE isn't /dev/null, else CPU).
# Omit DEVICE entirely to leave whatever is already in .env untouched.
_set_vss_device:
	@if [ -n "$(DEVICE)" ]; then \
		dev=$$(echo "$(DEVICE)" | tr '[:lower:]' '[:upper:]'); \
		case "$$dev" in \
			ALL) \
				. ./.env 2>/dev/null; \
				if [ -n "$$GPU_DEVICE" ] && [ "$$GPU_DEVICE" != "/dev/null" ]; then dev=GPU; \
				elif [ -n "$$NPU_DEVICE" ] && [ "$$NPU_DEVICE" != "/dev/null" ]; then dev=NPU; \
				else dev=CPU; fi; \
				echo "[up-vss] DEVICE=all -> auto-selected $$dev from hardware detected by 'make init'"; \
				;; \
			CPU|GPU|NPU) ;; \
			*) echo "ERROR: DEVICE must be one of CPU, GPU, NPU, all (got '$(DEVICE)')" >&2; exit 1 ;; \
		esac; \
		if [ "$$dev" = "GPU" ] && { [ -z "$$GPU_DEVICE" ] || [ "$$GPU_DEVICE" = "/dev/null" ]; }; then \
			echo "⚠  DEVICE=GPU requested but no GPU was detected by 'make init' (GPU_DEVICE=/dev/null)."; \
			echo "   Setting the VSS device variables to GPU anyway; containers will fail to start"; \
			echo "   without a real GPU_DEVICE/GPU_RENDER_DEVICE. Re-run 'make init' on GPU hardware first."; \
		fi; \
		if [ "$$dev" = "NPU" ] && { [ -z "$$NPU_DEVICE" ] || [ "$$NPU_DEVICE" = "/dev/null" ]; }; then \
			echo "⚠  DEVICE=NPU requested but no NPU was detected by 'make init' (NPU_DEVICE=/dev/null)."; \
			echo "   Setting the VSS device variables to NPU anyway; containers will fail to start"; \
			echo "   without a real NPU_DEVICE. Re-run 'make init' on NPU hardware first."; \
		fi; \
		sed -i "s|^VSS_INGEST_EMBEDDING_DEVICE=.*|VSS_INGEST_EMBEDDING_DEVICE=$$dev|" .env; \
		sed -i "s|^VSS_DETECTION_DEVICE=.*|VSS_DETECTION_DEVICE=$$dev|" .env; \
		sed -i "s|^VSS_QUERY_EMBEDDING_DEVICE=.*|VSS_QUERY_EMBEDDING_DEVICE=$$dev|" .env; \
		echo "✅ VSS_INGEST_EMBEDDING_DEVICE / VSS_DETECTION_DEVICE / VSS_QUERY_EMBEDDING_DEVICE set to $$dev in .env"; \
	fi

up-vss: init _set_vss_device _check_vss_env _check_certs _build_if_changed ## Relay mode + VSS search containers behind an nginx HTTPS proxy (enables Live Natural Language Search). DEVICE=CPU|GPU|NPU|all to pick the inference device
	@$(RESOLVE_PORTS) VSS_MANAGER_PORT:3001
	@set -a; . ./.env; set +a; \
	$(VSS_COMPOSE) up -d --no-build && \
	echo "" && echo "✅ UAV ground station is up. Open the dashboard at: https://$$VIZ_HOST_IP:$${VIZ_NGINX_HTTPS_PORT:-8443}"

down-vss: ## Stop the relay + VSS stack, keeping indexed footage/embeddings
	$(VSS_COMPOSE) down

# Index, footage and their metadata must be cleared together to stay consistent;
# the downloaded CLIP/YOLOX models (vss-ov-models, vss-yolox-models) are kept.
VSS_INDEX_VOLUMES = vss-vdms-db vss-minio-data vss-pg-data vss-data-prep

clean-vss-index: ## Stop the relay + VSS stack and DELETE indexed footage/embeddings, keeping models
	$(VSS_COMPOSE) down
	@project=$$($(VSS_COMPOSE) config | awk '/^name:/{print $$2; exit}'); \
	for v in $(VSS_INDEX_VOLUMES); do \
		docker volume rm -f "$${project}_$$v" >/dev/null && echo "removed $${project}_$$v"; \
	done

delete-vss-volumes: ## DELETE ALL VSS volumes incl. downloaded models (asks first; FORCE=1 skips)
	@if [ "$(FORCE)" != "1" ]; then \
		printf "This deletes ALL VSS volumes, including ~1.6 GB of downloaded models. Continue? [y/N] "; \
		read ans; case "$$ans" in [yY]|[yY][eE][sS]) ;; *) echo "Aborted."; exit 1;; esac; \
	fi
	$(VSS_COMPOSE) down -v

logs: ## Tail logs from all running containers
	docker compose -f docker-compose.yml -f docker-compose-mediamtx.yml logs -f

SSL_DIR = configs/nginx/ssl

_check_certs: ## Generate a self-signed TLS certificate for the nginx overlay if missing
	@mkdir -p $(SSL_DIR); \
	if [ ! -f "$(SSL_DIR)/server.crt" ] || [ ! -f "$(SSL_DIR)/server.key" ]; then \
		echo "Generate self-signed certificate..."; \
		. ./.env; \
		SAN="DNS:localhost,IP:127.0.0.1"; \
		if [ -n "$$VIZ_PUBLIC_HOST" ] && [ "$$VIZ_PUBLIC_HOST" != "127.0.0.1" ]; then SAN="$$SAN,IP:$$VIZ_PUBLIC_HOST"; fi; \
		openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
		  -keyout $(SSL_DIR)/server.key -out $(SSL_DIR)/server.crt \
		  -subj "/C=US/ST=CA/L=San Francisco/O=Intel/OU=Edge AI/CN=localhost" \
		  -addext "subjectAltName=$$SAN"; \
		chmod 600 $(SSL_DIR)/server.key; chmod 644 $(SSL_DIR)/server.crt; \
		docker restart uav-gs-nginx >/dev/null 2>&1 || true; \
	fi
