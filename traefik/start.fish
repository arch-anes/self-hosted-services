#!/bin/fish

set source_dir (dirname (readlink -m (status --current-filename)))

pushd $source_dir
touch acme.json && chmod 600 acme.json
docker network create proxy
docker-compose up -d
popd