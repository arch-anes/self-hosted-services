#!/bin/fish

set source_dir (dirname (readlink -m (status --current-filename)))

mkdir -p /storage/portainer

docker volume create -d local-persist -o mountpoint=/storage/portainer --name=portainer_data

pushd $source_dir
docker-compose up -d
popd