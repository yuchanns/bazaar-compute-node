"""Verify the official catalog in an independently installed manager release."""

from importlib.resources import files

from bazaar_contrib_manager.contracts import IndexDocument


def main() -> None:
    document = IndexDocument.model_validate_json(
        files("bazaar_contrib_manager").joinpath("catalog.json").read_bytes()
    )
    assert any(
        package.package == "bazaar-contrib-node-management"
        for package in document.contribs
    )
    print("Installed manager catalog includes the node-management contrib")


if __name__ == "__main__":
    main()
