destination=`pwd`/test_cases
rm -rf destination
mkdir -p test_cases
cd ckb-test-contracts/rust/acceptance-contracts
capsule build --release
release_dir=target/riscv64imac-unknown-none-elf/release/*
for file in ${release_dir}; do
    echo ${file}
    if [[ -f "$file" && ! "$file" == *.* ]]; then
        cp "$file" "$destination"
    fi
done