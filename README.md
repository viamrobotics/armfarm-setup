# armfarm-setup

Provisioning for the arm farm: `armfarmN` compute boxes running an xArm with a
wrist-mounted RealSense, registered in Viam under **hackathons > Fine Motor Skills**.

## Use it with Claude Code

```
git clone <this repo> && cd armfarm-setup
claude
```

Then `/armfarm-setup`. The skill walks the full provisioning flow; `CLAUDE.md` is loaded
automatically and carries the conventions and the known traps.

## Use it by hand

```
bash scripts/inspect.sh                          # read-only survey
sudo bash scripts/setup-host.sh armfarm7
sudo bash scripts/setup-network.sh
./scripts/provision-viam.py --name armfarm7 --cam-serial <serial> --wall left   # dry run
./scripts/provision-viam.py --name armfarm7 --cam-serial <serial> --wall left --apply --write-config
sudo systemctl restart viam-agent
```

Everything dry-runs by default and takes `--apply`.

## Requirements

- Viam CLI, authenticated as yourself (`viam login`)
- an org-scoped API key in `VIAM_API_KEY_ID` / `VIAM_API_KEY` for the Python scripts
- `pip install -r requirements.txt` for `provision-viam.py` and `update-fragments.py`
