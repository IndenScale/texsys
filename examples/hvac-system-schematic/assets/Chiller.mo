model Chiller
  parameter Real cooling_kw = 100; // kW, assumed
  parameter Real electric_kw = 25; // kW, assumed
  Real heat_rejection_kw; // kW
equation
  heat_rejection_kw = cooling_kw + electric_kw;
end Chiller;
