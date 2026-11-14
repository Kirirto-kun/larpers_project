"""Independent reference: exact rational Cramer's rule and analytic MSE."""
from fractions import Fraction
import math


def determinant(a):
    return (a[0][0]*(a[1][1]*a[2][2]-a[1][2]*a[2][1])
            - a[0][1]*(a[1][0]*a[2][2]-a[1][2]*a[2][0])
            + a[0][2]*(a[1][0]*a[2][1]-a[1][1]*a[2][0]))


def expected():
    n = 250000
    moments = [0]*5
    response = [0]*3
    yy = 0
    for index in range(n):
        value = index % 401 - 200
        target = 30 + 20*value - 5*value**2 + ((index*17) % 23 - 11)
        for degree in range(5):
            moments[degree] += value**degree
        for degree in range(3):
            response[degree] += target*value**degree
        yy += target*target
    normal = [[moments[row+column] for column in range(3)] for row in range(3)]
    denominator = determinant(normal)
    weights = []
    for column in range(3):
        modified = [row[:] for row in normal]
        for row in range(3):
            modified[row][column] = response[row]
        weights.append(Fraction(determinant(modified), 10*denominator))
    residual = Fraction(yy,100) - sum(weights[j]*Fraction(response[j],10) for j in range(3))
    return weights, residual/n


def verify(model):
    if (model.get("schema_version") != 1 or model.get("samples") != 250000
            or model.get("algorithm") != "quadratic-least-squares"):
        raise ValueError("model identity or dataset differs")
    coefficients = model.get("coefficients")
    if (not isinstance(coefficients,list) or len(coefficients) != 3
            or any(type(v) not in {int,float} or not math.isfinite(v) for v in coefficients)
            or type(model.get("training_mse")) not in {int,float}
            or not math.isfinite(model["training_mse"])):
        raise ValueError("invalid numerical model")
    reference, mse = expected()
    delta = max(abs(actual-float(wanted)) for actual,wanted in zip(coefficients,reference))
    mse_delta = abs(model["training_mse"]-float(mse))
    if delta > 1e-8 or mse_delta > 1e-8:
        raise ValueError("model differs from independent exact reference")
    return {"accepted":True,"reference":"exact-rational-cramers-rule",
            "max_coefficient_error":delta,"mse_error":mse_delta,
            "expected_coefficients":[float(v) for v in reference],"expected_training_mse":float(mse)}
