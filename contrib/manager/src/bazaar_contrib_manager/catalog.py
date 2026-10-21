"""Read every configured index and retain usable cached results independently."""

from __future__ import annotations

lazy import asyncio
lazy from datetime import UTC, datetime
lazy from hashlib import sha256
lazy from importlib.resources import files
lazy from pathlib import Path
lazy from urllib.parse import urlsplit
lazy from urllib.request import urlopen

from bazaar_core import ServiceKey
lazy from bazaar_contrib_config import ConfigDocument, Configuration
lazy from packaging.utils import canonicalize_name

from .contracts import CatalogEntry, IndexDocument, IndexStatus
lazy from .contracts import ManagementError, Package
lazy from .files import write


class ContribCatalog:
    def __init__(self, configuration: Configuration, path: Path) -> None:
        self.configuration = configuration
        self.path = path
        self._lock = asyncio.Lock()

    async def indexes(self) -> tuple[IndexStatus, ...]:
        candidate, _ = await self.configuration.read()
        document = ConfigDocument.model_validate(candidate.payload)
        async with self._lock:
            return tuple(
                await asyncio.gather(
                    *(
                        asyncio.to_thread(self._read, location)
                        for location in document.contrib_indexes
                    )
                )
            )

    def _read(self, location: str) -> IndexStatus:
        path = self.path / f"{sha256(location.encode()).hexdigest()}.json"
        try:
            if location.startswith(("http://", "https://")):
                with urlopen(location, timeout=20) as response:
                    raw = response.read()
            elif location.startswith("resource://"):
                resource = urlsplit(location)
                raw = (
                    files(resource.netloc)
                    .joinpath(resource.path.lstrip("/"))
                    .read_bytes()
                )
            else:
                raw = Path(location).expanduser().read_bytes()
            document = IndexDocument.model_validate_json(raw)
            status = IndexStatus(
                location=location,
                entries=document.contribs,
                read_at=datetime.now(UTC).isoformat(),
            )
            write(path, status.model_dump(mode="json"))
            return status
        except (ImportError, OSError, ValueError) as error:
            try:
                status = IndexStatus.model_validate_json(path.read_bytes())
            except OSError, ValueError:
                status = IndexStatus(location=location)
            return status.model_copy(
                update={"cached": bool(status.read_at), "error": str(error)}
            )

    async def entries(self) -> tuple[CatalogEntry, ...]:
        merged: dict[tuple[str, str], CatalogEntry] = {}
        for index in await self.indexes():
            for package in index.entries:
                key = package.package, package.source
                previous = merged.get(key)
                if previous is not None:
                    package = previous
                merged[key] = CatalogEntry(
                    **package.model_dump(exclude={"indexes"}),
                    indexes=(*previous.indexes, index.location)
                    if previous is not None
                    else (index.location,),
                )
        return tuple(
            sorted(merged.values(), key=lambda entry: (entry.package, entry.source))
        )

    async def select(
        self, name: str, *, source: str | None = None, version: str = ""
    ) -> Package:
        name = str(canonicalize_name(name))
        entries = await self.entries()
        names = {name, f"bazaar-contrib-{name}"}
        selected = [
            entry
            for entry in entries
            if entry.package in names and (source is None or entry.source == source)
        ]
        if len(selected) != 1:
            if not selected and source is not None:
                return Package(package=name, source=source, version=version)
            raise ManagementError(
                f"select one package/source for {name!r}; found {len(selected)} indexed choices"
            )
        return Package(
            package=selected[0].package,
            source=selected[0].source,
            version=version or selected[0].version,
        )


CONTRIB_CATALOG = ServiceKey[ContribCatalog]("contrib.catalog")
