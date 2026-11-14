"""Bounded, deterministic quadratic least-squares training diagnostic."""
import json
import platform
import sys
import time

started = time.perf_counter()
count = 250000
sums = [0] * 5
targets = [0] * 3
for i in range(count):
    x = i % 401 - 200
    y10 = 30 + 20*x - 5*x*x + (i*17) % 23 - 11
    power = 1
    for j in range(5):
        sums[j] += power
        if j < 3:
            targets[j] += power*y10
        power *= x
matrix = [[float(sums[r+c]) for c in range(3)] + [targets[r]/10] for r in range(3)]
for column in range(3):
    pivot = max(range(column, 3), key=lambda row: abs(matrix[row][column]))
    matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
    scale = matrix[column][column]
    matrix[column] = [v/scale for v in matrix[column]]
    for row in range(3):
        if row != column:
            scale = matrix[row][column]
            matrix[row] = [a-scale*b for a,b in zip(matrix[row], matrix[column])]
coefficients = [matrix[row][3] for row in range(3)]
error = 0.0
for i in range(count):
    x = i % 401 - 200
    actual = (30 + 20*x - 5*x*x + (i*17) % 23 - 11)/10
    predicted = sum(coefficients[j]*x**j for j in range(3))
    error += (actual-predicted)**2
result = {"schema_version":1,"algorithm":"quadratic-least-squares","samples":count,
          "coefficients":coefficients,"training_mse":error/count,
          "machine":platform.machine(),"system":platform.system(),
          "python":sys.version.split()[0],"elapsed_ms":round((time.perf_counter()-started)*1000,3)}
with open("model.json","w",encoding="utf-8") as output:
    json.dump(result,output,sort_keys=True,allow_nan=False)
print(json.dumps({"samples":count,"coefficients":coefficients,"training_mse":error/count}))
