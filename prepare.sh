set -ex

DEFAULT_FIBER_BRANCH="develop"
DEFAULT_FIBER_URL="https://github.com/nervosnetwork/fiber.git"

GitFIBERBranch="${GitBranch:-$DEFAULT_FIBER_BRANCH}"
GitFIBERUrl="${GitUrl:-$DEFAULT_FIBER_URL}"

cp download/0.117.0/ckb-cli ./source/ckb-cli
cp download/0.110.2/ckb-cli ./source/ckb-cli-old
git clone -b $GitFIBERBranch $GitFIBERUrl
cd fiber
cargo build
cp target/debug/fnn ../download/fiber/current/fnn.debug
cargo build --release
cp target/release/fnn ../download/fiber/current/fnn
cd migrate
cargo build
cp target/debug/fnn-migrate ../../download/fiber/current/fnn-migrate
