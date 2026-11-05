curl -X POST http://localhost:8080/action/arm
curl -sf -X POST http://localhost:8080/action/takeoff \
  -H "Content-Type: application/json" \
  -d '{"altitude": 10}'
sleep 120
curl -X POST http://localhost:8080/action/land
