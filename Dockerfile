# ShelfBlazer image. Same file for local builds and for CI.
#   docker buildx build --platform linux/amd64 -t shelfblazer:dev --load .
# Data and secrets are NOT in the image: mount the runtime home at /srv/shelfblazer (see deploy/compose.yaml).
FROM python:3.12-slim

# The record store commits every save with the git binary; attached files go to Git LFS (git-lfs).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git git-lfs \
 && rm -rf /var/lib/apt/lists/* \
 # The data folder is a bind mount owned by the host user: let git work in it whatever the uid.
 && git config --system safe.directory '*'

WORKDIR /build
COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt

COPY packages ./packages
COPY app ./app
RUN pip install --no-cache-dir --no-deps ./packages ./app \
 && cd / && rm -rf /build

# Runs as the uid:gid given by compose (the owner of the data folder); there is no fixed user in the image.
ENV HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    SHELFBLAZER_HOME=/srv/shelfblazer
WORKDIR /srv/shelfblazer
EXPOSE 8000

# One process only: the store relies on a file lock, do not add workers.
# Add --https only when TLS terminates in front of the container (it makes cookies Secure and breaks plain-HTTP login).
CMD ["shelfblazer", "run", "--host", "0.0.0.0", "--port", "8000"]
