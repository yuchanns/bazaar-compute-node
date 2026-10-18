# bazaar-contrib-legacy-node-migrations

Upgrades existing Node databases through the published migrations 1–32 using `SCHEMA`.
These steps share a version ledger and include changes across several business domains, so their order stays intact.

The `legacy-node-migrations` contrib completes that history before business contribs register their own schemas.
It reads the existing `bazaar-contrib-node-migrations` progress without changing those records, then continues under its installed distribution identity. Business registration transfers table ownership and adopts each package's baseline.
Fresh databases use the business contribs' baselines directly. Later migrations belong to each business package.
