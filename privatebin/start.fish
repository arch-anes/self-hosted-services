#!/bin/fish

set source_dir (dirname (readlink -m (status --current-filename)))

mkdir -p /storage/privatebin

docker volume create -d local-persist -o mountpoint=/storage/privatebin --name=privatebin_data

pushd $source_dir
docker-compose up -d
popd