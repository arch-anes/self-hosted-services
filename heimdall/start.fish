#!/bin/fish

set source_dir (dirname (readlink -m (status --current-filename)))

mkdir -p /storage/heimdall

docker volume create -d local-persist -o mountpoint=/storage/heimdall --name=heimdall_config

pushd $source_dir
docker-compose up -d
popd