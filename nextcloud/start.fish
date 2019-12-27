#!/bin/fish

set source_dir (dirname (readlink -m (status --current-filename)))

mkdir -p /storage/nextcloud{,app,data,db}

docker volume create -d local-persist -o mountpoint=/storage/nextcloud/app --name=nextcloud_app
docker volume create -d local-persist -o mountpoint=/storage/nextcloud/data --name=nextcloud_data
docker volume create -d local-persist -o mountpoint=/storage/nextcloud/db --name=nextcloud_db

pushd $source_dir
docker-compose up -d
popd