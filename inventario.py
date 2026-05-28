# inventario.py

productos = {
    # Bebidas
    "presidente": {"nombre": "Cerveza Presidente", "precio": 75, "cantidad": 50, "unidad": "unidad", "rebanado": False},
    "heineken": {"nombre": "Cerveza Heineken", "precio": 85, "cantidad": 30, "unidad": "unidad", "rebanado": False},
    "agua": {"nombre": "Agua fría", "precio": 25, "cantidad": 100, "unidad": "unidad", "rebanado": False},
    "jugo": {"nombre": "Jugo Tampico", "precio": 35, "cantidad": 40, "unidad": "unidad", "rebanado": False},
    "refresco": {"nombre": "Refresco Coca Cola", "precio": 55, "cantidad": 40, "unidad": "unidad", "rebanado": False},

    # Embutidos y lácteos
    "queso bola": {"nombre": "Queso de Bola", "precio": 120, "cantidad": 20, "unidad": "libra", "rebanado": True},
    "queso amarillo": {"nombre": "Queso Amarillo", "precio": 110, "cantidad": 20, "unidad": "libra", "rebanado": True},
    "jamon pierna": {"nombre": "Jamón de Pierna", "precio": 95, "cantidad": 15, "unidad": "libra", "rebanado": True},
    "jamon pavo": {"nombre": "Jamón de Pavo", "precio": 90, "cantidad": 15, "unidad": "libra", "rebanado": True},
    "salami": {"nombre": "Salami", "precio": 85, "cantidad": 25, "unidad": "libra", "rebanado": True},
    "mortadela": {"nombre": "Mortadela", "precio": 80, "cantidad": 20, "unidad": "libra", "rebanado": True},
    "longaniza": {"nombre": "Longaniza", "precio": 95, "cantidad": 15, "unidad": "libra", "rebanado": True},
    "leche": {"nombre": "Leche (funda)", "precio": 65, "cantidad": 35, "unidad": "unidad", "rebanado": False},
    "mantequilla": {"nombre": "Mantequilla", "precio": 75, "cantidad": 20, "unidad": "unidad", "rebanado": False},

    # Granos y víveres
    "arroz": {"nombre": "Arroz", "precio": 30, "cantidad": 100, "unidad": "libra", "rebanado": False},
    "habichuelas": {"nombre": "Habichuelas", "precio": 35, "cantidad": 50, "unidad": "libra", "rebanado": False},
    "azucar": {"nombre": "Azúcar", "precio": 25, "cantidad": 80, "unidad": "libra", "rebanado": False},
    "cafe": {"nombre": "Café Sandino", "precio": 85, "cantidad": 30, "unidad": "unidad", "rebanado": False},
    "cafe granel": {"nombre": "Café a granel", "precio": 90, "cantidad": 20, "unidad": "libra", "rebanado": False},
    "maiz": {"nombre": "Maíz", "precio": 20, "cantidad": 40, "unidad": "libra", "rebanado": False},
    "cebolla": {"nombre": "Cebolla", "precio": 25, "cantidad": 30, "unidad": "libra", "rebanado": False},
    "papa": {"nombre": "Papa", "precio": 30, "cantidad": 30, "unidad": "libra", "rebanado": False},

    # Otros básicos
    "aceite": {"nombre": "Aceite Iberia", "precio": 195, "cantidad": 20, "unidad": "unidad", "rebanado": False},
    "huevo": {"nombre": "Huevo (unidad)", "precio": 15, "cantidad": 80, "unidad": "unidad", "rebanado": False},
    "pan": {"nombre": "Pan de agua", "precio": 10, "cantidad": 45, "unidad": "unidad", "rebanado": False},
    "platano": {"nombre": "Plátano", "precio": 10, "cantidad": 50, "unidad": "unidad", "rebanado": False},
    "cigarrillo": {"nombre": "Cigarrillos", "precio": 25, "cantidad": 60, "unidad": "unidad", "rebanado": False},
}

# Alias dominicanos — formas alternativas de escribir productos
# Incluye sin "s" al final, errores comunes, abreviaciones
ALIAS = {
    # Presidente
    "presidente": "presidente",
    "presidentes": "presidente",
    "presiden": "presidente",
    "presi": "presidente",

    # Heineken
    "heineken": "heineken",
    "heini": "heineken",
    "heines": "heineken",

    # Agua
    "agua": "agua",
    "agüita": "agua",
    "aguita": "agua",

    # Refresco
    "refresco": "refresco",
    "refrescos": "refresco",
    "coca": "refresco",
    "cocacola": "refresco",
    "coca cola": "refresco",

    # Jugo
    "jugo": "jugo",
    "jugos": "jugo",
    "tampico": "jugo",

    # Salami
    "salami": "salami",
    "salamis": "salami",
    "salame": "salami",

    # Queso
    "queso bola": "queso bola",
    "queso de bola": "queso bola",
    "queso amarillo": "queso amarillo",
    "queso": "queso amarillo",

    # Jamón
    "jamon": "jamon pierna",
    "jamón": "jamon pierna",
    "jamon pierna": "jamon pierna",
    "jamon pavo": "jamon pavo",
    "jamón pavo": "jamon pavo",

    # Mortadela
    "mortadela": "mortadela",
    "mortadelas": "mortadela",
    "mortade": "mortadela",

    # Longaniza
    "longaniza": "longaniza",
    "longanizas": "longaniza",
    "longani": "longaniza",

    # Arroz
    "arroz": "arroz",

    # Habichuelas
    "habichuelas": "habichuelas",
    "habichuela": "habichuelas",
    "frijoles": "habichuelas",
    "frijol": "habichuelas",

    # Azúcar
    "azucar": "azucar",
    "azúcar": "azucar",

    # Café
    "cafe": "cafe",
    "café": "cafe",
    "sandino": "cafe",
    "cafe granel": "cafe granel",
    "café granel": "cafe granel",

    # Aceite
    "aceite": "aceite",
    "iberia": "aceite",

    # Huevo
    "huevo": "huevo",
    "huevos": "huevo",
    "huevo": "huevo",

    # Pan
    "pan": "pan",
    "panes": "pan",
    "pan de agua": "pan",

    # Plátano
    "platano": "platano",
    "plátano": "platano",
    "platanos": "platano",
    "plátanos": "platano",
    "guineo": "platano",

    # Leche
    "leche": "leche",
    "lechita": "leche",

    # Mantequilla
    "mantequilla": "mantequilla",
    "mantequillas": "mantequilla",
    "mante": "mantequilla",

    # Cigarrillos
    "cigarrillo": "cigarrillo",
    "cigarrillos": "cigarrillo",
    "cigarro": "cigarrillo",
    "cigarros": "cigarrillo",
    "fuma": "cigarrillo",

    # Maíz
    "maiz": "maiz",
    "maíz": "maiz",

    # Cebolla
    "cebolla": "cebolla",
    "cebollas": "cebolla",

    # Papa
    "papa": "papa",
    "papas": "papa",
    "patata": "papa",
}

# Palabras que se ignoran al buscar productos
PALABRAS_IGNORAR = [
    "dame", "quiero", "necesito", "ponme", "mandame", "mándame",
    "tráeme", "traeme", "deme", "déme", "una", "uno", "un",
    "por favor", "porfavor", "fa", "xfavor", "libra", "libras",
    "media", "medio", "1/2", "1/4", "3/4", "de", "unidad",
    "unidades", "dos", "tres", "cuatro", "cinco", "seis",
    "siete", "ocho", "nueve", "diez", "un cuarto", "tres cuartos",
    "y", "con", "mas", "más", "también", "tambien"
]


def normalizar_texto(texto):
    """
    Normaliza el texto eliminando palabras irrelevantes
    y aplicando correcciones comunes del español dominicano.
    """
    texto = texto.lower().strip()

    # Elimina palabras a ignorar
    for palabra in sorted(PALABRAS_IGNORAR, key=len, reverse=True):
        texto = texto.replace(palabra, " ")

    # Elimina números al inicio
    palabras = texto.split()
    if palabras and palabras[0].replace('.', '', 1).isdigit():
        palabras.pop(0)
    texto = " ".join(palabras).strip()

    # Elimina espacios dobles
    while "  " in texto:
        texto = texto.replace("  ", " ")

    return texto.strip()


def buscar_por_alias(texto):
    """Busca un producto usando el diccionario de alias."""
    texto = texto.strip()

    # Búsqueda exacta en alias
    if texto in ALIAS:
        clave = ALIAS[texto]
        if clave in productos:
            return clave, productos[clave]

    # Búsqueda parcial en alias
    for alias, clave in ALIAS.items():
        if alias in texto and clave in productos:
            return clave, productos[clave]

    # Búsqueda directa en productos
    for clave, producto in productos.items():
        if clave in texto or producto["nombre"].lower() in texto:
            return clave, producto

    return None, None


def parsear_cantidad(texto):
    """
    Extrae la cantidad del texto.
    Soporta números, fracciones y palabras en español.
    Retorna (cantidad_float, texto_display)
    """
    texto = texto.lower().strip()

    if any(p in texto for p in ["1/4", "un cuarto", "cuarto"]):
        return 0.25, "1/4 libra (un cuarto)"
    if any(p in texto for p in ["3/4", "tres cuartos", "tres cuarto"]):
        return 0.75, "3/4 libra (tres cuartos)"
    if any(p in texto for p in ["1/2", "media", "medio"]):
        return 0.5, "1/2 libra (media libra)"

    numeros_escritos = {
        "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
        "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10
    }
    for palabra, valor in numeros_escritos.items():
        if palabra in texto.split():
            return float(valor), str(valor)

    palabras = texto.split()
    for palabra in palabras:
        try:
            valor = float(palabra)
            return valor, str(int(valor)) if valor == int(valor) else str(valor)
        except ValueError:
            continue

    return 1.0, "1"


def buscar_producto(texto):
    """Busca un producto en el texto y extrae su cantidad."""
    texto_original = texto.lower().strip()
    cantidad, cantidad_texto = parsear_cantidad(texto_original)
    texto_limpio = normalizar_texto(texto_original)
    clave, producto = buscar_por_alias(texto_limpio)
    return clave, producto, cantidad, cantidad_texto


def parsear_linea_multiple(texto):
    """
    Parsea múltiples productos en una línea.
    Ejemplo: "dame 2 presidente, media libra de salami y 1 libra de arroz"
    """
    separadores = [",", " y "]
    items_texto = [texto]
    for sep in separadores:
        nuevos = []
        for item in items_texto:
            nuevos.extend(item.split(sep))
        items_texto = nuevos

    resultados = []
    for item in items_texto:
        item = item.strip()
        if not item:
            continue
        clave, producto, cantidad, cantidad_texto = buscar_producto(item)
        if producto:
            resultados.append((clave, producto, cantidad, cantidad_texto))

    return resultados


def verificar_disponibilidad(clave, cantidad=1):
    if clave in productos:
        return productos[clave]["cantidad"] >= cantidad
    return False


def reducir_inventario(clave, cantidad=1):
    if clave in productos and productos[clave]["cantidad"] >= cantidad:
        productos[clave]["cantidad"] -= cantidad
        return True
    return False