# unixnu-droidvm-ci

GitHub Actions builds for porting [UniXNU](https://github.com/UniXNU) to QEMU/KVM `virt` and to
[DroidVM](https://github.com/Droid-VM) crosvm on Gunyah.

- `.github/workflows/kernel.yml`: `unixnu build kernel` on a macOS runner.
- `device/UniXNU-droidvm`: device repository of the virtual board.
- `local/`: local overrides of UniXNU modules, same layout as the UniXNU repository.

Binary artifacts contain components of Apple's kernelcache and are uploaded encrypted. Decrypt with:

```sh
openssl enc -d -aes-256-cbc -pbkdf2 -pass file:KEYFILE -in binaries.tgz.enc -out binaries.tgz
```
