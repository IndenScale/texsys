model CoolingTower
  parameter Real heat_rejection_kw = 125; // kW, assumed
  parameter Real mass_flow_kg_s = 5.97; // kg/s, assumed
  parameter Real cp_kj_kgk = 4.186; // kJ/(kg.K), assumed
  Real delta_temperature_k; // K
equation
  delta_temperature_k = heat_rejection_kw / (mass_flow_kg_s * cp_kj_kgk);
end CoolingTower;
