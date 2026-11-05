model CoolingLoad
  parameter Real load_kw = 100; // kW, assumed
  parameter Real mass_flow_kg_s = 4.78; // kg/s, assumed
  parameter Real cp_kj_kgk = 4.186; // kJ/(kg.K), assumed
  Real delta_temperature_k; // K
equation
  delta_temperature_k = load_kw / (mass_flow_kg_s * cp_kj_kgk);
end CoolingLoad;
