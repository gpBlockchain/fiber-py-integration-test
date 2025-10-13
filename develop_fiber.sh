set -e
# git clone https://github.com/nervosnetwork/ckb-cli.git
# cd ckb-cli
# git checkout pkg/v1.7.0
# make prod
# cp target/release/ckb-cli ../source/ckb-cli
# cd ../
DEFAULT_FIBER_BRANCH="develop"
DEFAULT_FIBER_URL="https://github.com/nervosnetwork/fiber.git"
DEFAULT_BUILD_FIBER=true


GitFIBERBranch="${GitBranch:-$DEFAULT_FIBER_BRANCH}"
GitFIBERUrl="${GitUrl:-$DEFAULT_FIBER_URL}"
BUILD_FIBER="${BuildFIBER:-$DEFAULT_BUILD_FIBER}"
if [ "$BUILD_FIBER" == "true" ]; then
  git clone -b $GitFIBERBranch $GitFIBERUrl
  cd fiber
  cargo build --release
  cp target/release/fnn ../download/fiber/current/fnn
  cd migrate
  cargo build
  cp target/debug/fnn-migrate ../../download/fiber/current/fnn-migrate
  cd ../../
fi