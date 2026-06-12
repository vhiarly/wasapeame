"""
Blueprint Cliente — Dashboard para dueños de negocio
Rutas: /cliente/*, /cliente/api/*
"""

import json
from functools import wraps
from flask import Blueprint, render_template, request, session, redirect, jsonify
from db import execute

cliente_bp = Blueprint('cliente', __name__, url_prefix='/cliente')


def require_cliente(f):
    """Decorator para verificar sesión cliente"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        codigo = session.get('cliente_codigo')
        if not codigo:
            return redirect('/cliente')
        return f(codigo, *args, **kwargs)
    return decorated_function


@cliente_bp.route('', methods=['GET', 'POST'])
def login():
    """Login cliente con código + PIN"""
    if request.method == 'POST':
        codigo = request.form.get('codigo', '').upper()
        pin = request.form.get('pin', '')

        try:
            negocio = execute(
                "SELECT codigo, nombre, pin FROM negocios WHERE codigo = %s",
                (codigo,),
                fetch='one'
            )

            if negocio and negocio[2] == pin:
                session['cliente_codigo'] = codigo
                return redirect('/cliente/dashboard')
            else:
                return render_template('cliente/login.html', error='Código o PIN inválido')
        except:
            return render_template('cliente/login.html', error='Código o PIN inválido')

    return render_template('cliente/login.html')


@cliente_bp.route('/dashboard')
@require_cliente
def dashboard(codigo):
    """Dashboard del negocio"""
    negocio = execute(
        "SELECT nombre, modo FROM negocios WHERE codigo = %s",
        (codigo,),
        fetch='one'
    )
    if not negocio:
        return "Negocio no encontrado", 404

    return render_template('cliente/dashboard.html', codigo=codigo, negocio_nombre=negocio[0], modo=negocio[1])


@cliente_bp.route('/api/stats')
@require_cliente
def api_stats(codigo):
    """JSON: Estadísticas del negocio desde dashboard_stats"""
    result = execute(
        "SELECT stats FROM dashboard_stats WHERE codigo = %s",
        (codigo,),
        fetch='one'
    )
    if result and result[0]:
        return jsonify(json.loads(result[0]))
    return jsonify({"error": "Sin datos aún"}), 404


@cliente_bp.route('/api/pedidos')
@require_cliente
def api_pedidos(codigo):
    """JSON: Pedidos activos + recientes"""
    # Activos
    activos = execute(
        "SELECT numero_cliente, estado, items, total, actualizado_en FROM conversaciones_pedidos WHERE codigo = %s ORDER BY actualizado_en DESC LIMIT 10",
        (codigo,),
        fetch='all'
    )

    result = []
    for p in activos or []:
        result.append({
            "numero": p[0],
            "estado": p[1],
            "items": json.loads(p[2]) if p[2] else [],
            "total": float(p[3]) if p[3] else 0,
            "actualizado": str(p[4]),
            "tipo": "activo"
        })

    # Recientes
    recientes = execute(
        "SELECT numero_cliente, estado, items, total, creado_en FROM pedidos WHERE codigo = %s ORDER BY creado_en DESC LIMIT 20",
        (codigo,),
        fetch='all'
    )

    for p in recientes or []:
        result.append({
            "numero": p[0],
            "estado": p[1],
            "items": json.loads(p[2]) if p[2] else [],
            "total": float(p[3]) if p[3] else 0,
            "creado": str(p[4]),
            "tipo": "reciente"
        })

    return jsonify(result)


@cliente_bp.route('/api/citas')
@require_cliente
def api_citas(codigo):
    """JSON: Citas del día + próximas"""
    # Activas
    activas = execute(
        "SELECT numero_cliente, servicio, dia, hora, actualizado_en FROM conversaciones_citas WHERE codigo = %s ORDER BY actualizado_en DESC LIMIT 10",
        (codigo,),
        fetch='all'
    )

    result = []
    for c in activas or []:
        result.append({
            "numero": c[0],
            "servicio": c[1],
            "dia": c[2],
            "hora": c[3],
            "actualizado": str(c[4]),
            "tipo": "activa"
        })

    # Confirmadas
    confirmadas = execute(
        "SELECT numero_cliente, nombre_servicio, fecha, hora, estado, creado_en FROM citas WHERE codigo = %s ORDER BY fecha DESC LIMIT 20",
        (codigo,),
        fetch='all'
    )

    for c in confirmadas or []:
        result.append({
            "numero": c[0],
            "servicio": c[1],
            "fecha": str(c[2]),
            "hora": c[3],
            "estado": c[4],
            "creado": str(c[5]),
            "tipo": "confirmada"
        })

    return jsonify(result)


@cliente_bp.route('/api/catalogo')
@require_cliente
def api_catalogo(codigo):
    """JSON: Productos/servicios del negocio"""
    negocio = execute(
        "SELECT modo FROM negocios WHERE codigo = %s",
        (codigo,),
        fetch='one'
    )
    if not negocio:
        return jsonify({"error": "Negocio no encontrado"}), 404

    modo = negocio[0]

    if modo == 'pedidos':
        items = execute(
            "SELECT id, nombre, precio, unidad, cantidad, activo FROM catalogo WHERE codigo = %s ORDER BY nombre",
            (codigo,),
            fetch='all'
        )
        return jsonify([{
            "id": i[0],
            "nombre": i[1],
            "precio": float(i[2]),
            "unidad": i[3],
            "cantidad": i[4],
            "activo": i[5]
        } for i in items or []])
    else:
        items = execute(
            "SELECT id, nombre, precio, duracion_minutos, activo FROM servicios WHERE codigo = %s ORDER BY nombre",
            (codigo,),
            fetch='all'
        )
        return jsonify([{
            "id": i[0],
            "nombre": i[1],
            "precio": float(i[2]),
            "duracion": i[3],
            "activo": i[4]
        } for i in items or []])


@cliente_bp.route('/api/catalogo/<int:item_id>', methods=['POST'])
@require_cliente
def api_catalogo_update(codigo, item_id):
    """Actualizar precio/cantidad/estado de producto"""
    data = request.json
    precio = data.get('precio')
    cantidad = data.get('cantidad')
    activo = data.get('activo')

    negocio = execute(
        "SELECT modo FROM negocios WHERE codigo = %s",
        (codigo,),
        fetch='one'
    )
    if not negocio:
        return jsonify({"error": "Negocio no encontrado"}), 404

    modo = negocio[0]

    if modo == 'pedidos':
        if precio is not None:
            execute("UPDATE catalogo SET precio = %s WHERE id = %s AND codigo = %s", (precio, item_id, codigo))
        if cantidad is not None:
            execute("UPDATE catalogo SET cantidad = %s WHERE id = %s AND codigo = %s", (cantidad, item_id, codigo))
        if activo is not None:
            execute("UPDATE catalogo SET activo = %s WHERE id = %s AND codigo = %s", (activo, item_id, codigo))
    else:
        if precio is not None:
            execute("UPDATE servicios SET precio = %s WHERE id = %s AND codigo = %s", (precio, item_id, codigo))
        if activo is not None:
            execute("UPDATE servicios SET activo = %s WHERE id = %s AND codigo = %s", (activo, item_id, codigo))

    return jsonify({"ok": True})


@cliente_bp.route('/api/horarios')
@require_cliente
def api_horarios(codigo):
    """JSON: Horarios del negocio"""
    horarios = execute(
        "SELECT dia, trabaja, inicio, fin FROM horarios WHERE codigo = %s ORDER BY dia",
        (codigo,),
        fetch='all'
    )

    dias_orden = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']

    return jsonify([{
        "dia": h[0],
        "trabaja": h[1],
        "inicio": h[2],
        "fin": h[3]
    } for h in horarios or []])


@cliente_bp.route('/api/horarios/<dia>', methods=['POST'])
@require_cliente
def api_horarios_update(codigo, dia):
    """Actualizar horario de un día"""
    data = request.json
    trabaja = data.get('trabaja', True)
    inicio = data.get('inicio')
    fin = data.get('fin')

    execute(
        "UPDATE horarios SET trabaja = %s, inicio = %s, fin = %s WHERE codigo = %s AND dia = %s",
        (trabaja, inicio, fin, codigo, dia)
    )

    return jsonify({"ok": True})
