model ChilledWaterPump
  parameter Real flow_m3_h = 17.2; // m3/h, assumed
  parameter Real head_m = 20; // m, assumed
  Real hydraulic_kw; // kW
equation
  hydraulic_kw = flow_m3_h / 3600 * 1000 * 9.80665 * head_m / 1000;
end ChilledWaterPump;
