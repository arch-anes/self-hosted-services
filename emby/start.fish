#!/bin/fish

set source_dir (dirname (readlink -m (status --current-filename)))

mkdir -p /storage/emby
pushd /storage/emby
mkdir -p data config
popd

docker volume create -d local-persist -o mountpoint=/storage/emby/config --name=emby_config
docker volume create -d local-persist -o mountpoint=/storage/emby/data --name=emby_data

pushd $source_dir
docker-compose up -d
popd