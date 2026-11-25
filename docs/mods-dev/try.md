# Try a mod you are writing: `mods dev`

Part of [Writing mods for XY Forever](../MODS-DEV.md) (#204).

```sh
uv run xyf-host mods dev DIR/mods/opxy-1.2.1/<generation>/<mod>   # or the generation folder with --only ID
```

Run `modkit split` on your kit first. `mods dev` waits for the OP-XY's XY Forever menu (the serial port appearing:
with Settings > Menu at power-on set to Always, turning the OP-XY on is the only thing to do at the device), then,
each phase printed with its time:

- **push:** the mod and its base as a development set, in `mods/dev-<generation>/` (only changed files are sent;
  older development sets are removed). It never mixes with your saved mods, whatever kit generation it is from, and
  the Mods page does not list it ([the store](../storage/mods-store.md)).
- **build:** the mod alone, on the saved base and Mod memory, into `mods-a` or `mods-b` (the slot your saved
  mods are not in). Every new version builds again.
- **boot:** its content installed, then it boots once (`menu.converge` mode 2).

Nothing saved changes: the selection, Mod memory and the default stay. While the build is in use, the device's and the
web app's Mods pages and `mods status` say so. The next plain power-on goes back to your saved setup by itself
([convergence](../architecture/boot/convergence.md)); with Menu at power-on set to Always the menu opens instead (its
first row reads "(to apply)"), ready for the next `mods dev`. A device refusal is printed in plain words; a base
XY Forever does not know needs `--unverified`; `--base ID` names the stock entry when nothing is saved.
