#!/bin/fish

set source_dir (dirname (readlink -m (status --current-filename)))

mkdir -p /storage/jellyfin/{,media,config}

docker volume create -d local-persist -o mountpoint=/storage/jellyfin/config --name=jellyfin_config
docker volume create -d local-persist -o mountpoint=/storage/jellyfin/media --name=jellyfin_media

pushd $source_dir
docker-compose up -d
popd