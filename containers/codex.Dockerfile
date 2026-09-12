FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5
RUN apt-get update && apt-get install -y --no-install-recommends python3 git \
    && rm -rf /var/lib/apt/lists/* && ln -s /usr/bin/python3 /usr/local/bin/python
RUN npm install --global @openai/codex@0.153.4 && npm cache clean --force
USER 65534:65534
