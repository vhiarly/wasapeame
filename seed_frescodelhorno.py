"""
Corre una sola vez:
  DATABASE_URL=postgresql://... python3 seed_frescodelhorno.py
"""
import os
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur  = conn.cursor()

# ── Negocio ───────────────────────────────────────────────────────
cur.execute("""
    INSERT INTO negocios (codigo, nombre, descripcion, modo, tipo, activo,
                          numero_negocio, pin, requiere_comprobante, instrucciones_pago)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (codigo) DO NOTHING
""", (
    "PA1",
    "Fresco del Horno Pastelería",
    "Pastelería artesanal en Santo Domingo. Pasteles, postres, bocadillos y combos hechos con amor.",
    "pedidos",
    "PA",
    True,
    "whatsapp:+18293993344",
    "4821",
    False,
    "Pago al recoger.",
))

# ── Horarios ──────────────────────────────────────────────────────
horarios = [
    ("lunes",     True,  "08:00", "19:00"),
    ("martes",    True,  "08:00", "19:00"),
    ("miercoles", True,  "08:00", "19:00"),
    ("jueves",    True,  "08:00", "19:00"),
    ("viernes",   True,  "08:00", "19:00"),
    ("sabado",    True,  "08:00", "16:00"),
    ("domingo",   False, None,    None),
]
for dia, trabaja, inicio, fin in horarios:
    cur.execute("""
        INSERT INTO horarios (codigo, dia, trabaja, inicio, fin)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (codigo, dia) DO NOTHING
    """, ("PA1", dia, trabaja, inicio, fin))

# ── Catálogo ──────────────────────────────────────────────────────
# (clave, nombre, precio)
productos = [
    # TEMPORADA
    ("temp_manjar_mango_naranjas",      "Manjar de Mango y Naranjas",                   0),
    ("temp_lemon_pie_cake",             "Lemon Pie Cake 8-12 personas",              3500),
    ("temp_manjar_molocoton_ponche",    "Manjar de Melocotón y Ponche",                 0),
    ("temp_cakevlova",                  "Cakevlova 8-12 Personas",                   2500),
    ("temp_flores_comestibles_tope",    "Pastel Flores Comestibles - Solo tope",        0),
    ("temp_flores_comestibles_caja",    "Pastel Flores Comestibles en Caja",            0),
    ("temp_cajita_2_brownies",          "Cajita Cuadrada 2 Mini Brownies",            200),
    ("temp_cajita_1_brownie",           "Cajita 1 Mini Brownie",                      150),
    ("temp_cupcake_dulce_leche",        "Cajita Cupcake Dulce de Leche Amaretto",     200),

    # PASTELES ENTEROS
    ("pastel_pina_colada",              "Piña Colada",                                  0),
    ("pastel_crema_pastelera",          "Crema Pastelera",                              0),
    ("pastel_dulce_de_leche",           "Dulce de Leche",                               0),
    ("pastel_chocolate_dulce_leche",    "Chocolate con Dulce de Leche",                 0),
    ("pastel_black_and_white",          "Black and White",                              0),
    ("pastel_manjar_pina",              "Manjar de Piña",                               0),
    ("pastel_crema_especial",           "Crema Especial",                               0),
    ("pastel_nutella",                  "Especial de Nutella",                          0),
    ("pastel_turron_pistacho",          "Turrón de Pistacho",                           0),
    ("pastel_manjar_coco",              "Manjar de Coco",                               0),
    ("pastel_turron_almendras",         "Turrón de Almendras",                          0),
    ("pastel_black_white_turron",       "Black and White con Turrón",                   0),
    ("pastel_ciruela",                  "Ciruela",                                      0),
    ("pastel_ciruela_dulce_leche",      "Ciruela con Dulce de Leche",                   0),
    ("pastel_guayaba_dulce_leche",      "Guayaba con Dulce de Leche",                   0),
    ("pastel_pina_dulce_leche",         "Piña con Dulce de Leche",                      0),
    ("pastel_milkyway",                 "Milkyway",                                     0),
    ("pastel_mermelada_guayaba",        "Mermelada de Guayaba",                         0),
    ("pastel_crema_almendras",          "Crema de Almendras",                           0),
    ("pastel_snickers_madness",         "Snickers Madness",                             0),
    ("pastel_manjar_nutella_pistacho",  "Manjar de Nutella y Turrón de Pistacho",       0),
    ("pastel_chocococo_madness",        "Choco-coco Madness",                           0),
    ("pastel_torta_alejandro",          "Torta Alejandro",                              0),
    ("pastel_manjar_blueberry",         "Manjar de Blueberry",                          0),
    ("pastel_nutella_turron",           "Nutella con Turrón",                           0),
    ("pastel_bw_turron_pistacho",       "Black and White con Turrón de Pistacho",       0),

    # POSTRES
    ("postre_volteado_pina",            "Volteado de Piña",                             0),
    ("postre_cheesecake_pistacho",      "Cheesecake Pistacho con Nutella",              0),
    ("postre_cheesecake_red_velvet",    "Cheesecake Red Velvet",                     2700),
    ("postre_cheesecake_pistacho2",     "Cheesecake Topping de Pistacho",               0),
    ("postre_cheesecake_dulce_leche",   "Cheesecake de Dulce de Leche",                 0),
    ("postre_cheesecake_fresa",         "Cheesecake de Fresa",                          0),
    ("postre_cheesecake_oreo",          "Cheesecake de Oreo",                           0),
    ("postre_cheesecake_brownie",       "Cheesecake de Brownie",                        0),
    ("postre_cheesecake_zanahorias",    "Cheesecake de Zanahorias y Coco",              0),
    ("postre_cheesecake_mango",         "Cheesecake Topping de Mango",                  0),
    ("postre_pudin_pan",                "Pudín de Pan",                              1450),
    ("postre_flan_imposible",           "Flan Imposible",                            1750),
    ("postre_flan_leche",               "Flan de Leche",                             1450),
    ("postre_tres_leches",              "Tres Leches Grande 8-10 personas",          1950),
    ("postre_cuatro_leches",            "Cuatro Leches Grande 8-10 personas",        2050),
    ("postre_tres_leches_pistacho",     "Tres Leches Pistacho Grande 8-10 personas", 2050),
    ("postre_tres_leches_ciruela",      "Tres Leches Ciruela Grande 8-10 personas",  2050),
    ("postre_pie_limon",                "Pie de Limón",                              1950),

    # PASTELES ESPECIALES
    ("esp_manjar_naranjas",             "Manjar de Naranjas",                           0),
    ("esp_pasion_cake",                 "Pasion Cake",                                  0),
    ("esp_coconut_carrot",              "Coconut Carrot Cake",                          0),
    ("esp_tiramisu_nutella",            "Tiramisu con Nutella",                         0),
    ("esp_tornado_chocolate",           "Tornado de Chocolate",                         0),
    ("esp_tornado_chinola",             "Tornado de Chinola",                           0),
    ("esp_red_velvet",                  "Red Velvet",                                   0),
    ("esp_delicia_tropical",            "Delicia Tropical",                             0),
    ("esp_mil_hojas",                   "Mil Hojas Crema Pastelera y Dulce de Leche",1650),
    ("esp_tarta_valeria",               "Tarta Valeria",                                0),
    ("esp_flores_comestibles",          "Pastel Flores Comestibles",                    0),

    # BOCADILLOS
    ("boc_pastelitos_pollo",            "12 Pastelitos de Pollo",                     480),
    ("boc_pastelitos_queso",            "12 Pastelitos de Queso",                     480),
    ("boc_empanaditas_queso_puerro",    "12 Empanaditas Queso Crema y Puerro",        540),
    ("boc_quipes_res",                  "12 Quipes de Res",                           720),
    ("boc_croquetas_pollo",             "12 Croquetas de Pollo",                      600),
    ("boc_pizzitas",                    "12 Pizzitas",                                720),
    ("boc_sanduchitos",                 "12 Mini Sanduchitos Queso Crema y Puerro",   540),
    ("boc_catibias_queso",              "6 Catibias de Queso Mozzarella",             420),
    ("boc_catibias_pollo",              "6 Catibias de Pollo",                        420),
    ("boc_croissant_jamon_queso",       "6 Croissant de Jamón y Queso",               960),
    ("boc_croissant_queso_crema",       "6 Croissant de Queso Crema",                 960),

    # COMBOS / CONGELADOS
    ("combo_picadera",                  "Combo Picadera Congelada",                  2250),
    ("combo_pastelitos_pollo_cong",     "12 Pastelitos de Pollo Congelados",          480),
    ("combo_pastelitos_queso_cong",     "12 Pastelitos de Queso Congelados",          480),
    ("combo_quipes_cong",               "12 Quipes de Res Congelados",                720),
    ("combo_croquetas_cong",            "12 Croquetas de Pollo Congeladas",           600),
    ("combo_croquetas_queso_cong",      "12 Croquetas de Pollo Rellenas de Queso",   720),
    ("combo_keto_cong",                 "12 Croquetas KETO Cepa de Apio",           1200),
    ("combo_empanaditas_cong",          "12 Empanaditas Queso Crema y Puerro Cong.", 540),
    ("combo_pizzitas_cong",             "15 Pizzitas To Go para Preparar en Casa",   900),
    ("combo_catibias_pollo_cong",       "12 Catibias de Pollo Congeladas",            840),
    ("combo_catibias_queso_cong",       "12 Catibias de Queso Mozzarella Cong.",      840),

    # LUNCH BOX
    ("lb_18",                           "Lunch Box 18 unidades",                      800),
    ("lb_16",                           "Lunch Box 16 unidades",                      600),
]

for clave, nombre, precio in productos:
    cur.execute("""
        INSERT INTO catalogo (codigo, clave, nombre, precio, unidad, rebanado, activo, cantidad)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (codigo, clave) DO NOTHING
    """, ("PA1", clave, nombre, precio, "unidad", False, True, 99))
    print(f"  ✓ {nombre}")

cur.execute("""
    INSERT INTO contadores_turnos (codigo, contador) VALUES (%s, 0)
    ON CONFLICT (codigo) DO NOTHING
""", ("PA1",))

conn.commit()
cur.close()
conn.close()
print(f"\nPA1 — Fresco del Horno creado con {len(productos)} productos.")
