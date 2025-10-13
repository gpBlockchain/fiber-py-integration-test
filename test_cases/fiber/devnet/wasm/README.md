### prepare 
start wasm version fiber
```angular2html
export FIBER_REPO=https://github.com/nervosnetwork/fiber.git
export FIBER_BRANCH=develop
git clone https://github.com/gpBlockchain/fiber-wasm-demo.git
bash prepare.sh
```

stop wasm versipn fiber
```angular2html
kill -9 $(lsof -ti:8000)
kill -9 $(lsof -ti:9000)
```

restart wasm version fiber
```angular2html
python3 server.py > server.log 2>&1 &
echo "run e2e"
cd fiber-wasm-client-rpc
npm run service > service.log 2>&1 &
```