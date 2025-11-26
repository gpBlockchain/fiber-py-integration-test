set -ex

DEFAULT_FIBER_BRANCH="v0.6.0"
DEFAULT_FIBER_URL="https://github.com/nervosnetwork/fiber.git"

GitFIBERBranch="${GitBranch:-$DEFAULT_FIBER_BRANCH}"
GitFIBERUrl="${GitUrl:-$DEFAULT_FIBER_URL}"

cp download/0.202.0/ckb-cli ./source/ckb-cli
git clone -b $GitFIBERBranch $GitFIBERUrl
cd fiber
cargo build
cp target/debug/fnn ../download/fiber/current/fnn.debug
cargo build --release
cp target/release/fnn ../download/fiber/current/fnn
