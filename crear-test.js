const { Pool } = require('pg');

// Intentamos jalar la base de datos desde el .env si existe
const pool = new Pool({
  connectionString: process.env.DATABASE_URL || 'postgresql://vhiarly:tu_password_aqui@localhost:5432/wasapeame'
});

async function crearNegocioTest() {
  try {
    const query = `
      INSERT INTO negocios (codigo, nombre, telefono, estado) 
      VALUES ('TEST', 'Negocio de Citas Prueba', '8095551234', 'activo')
      ON CONFLICT (codigo) DO NOTHING;
    `;
    await pool.query(query);
    console.log('✅ ¡Éxito! El comercio "TEST" está listo en la base de datos.');
  } catch (error) {
    console.error('❌ Error al insertar:', error.message);
    console.log('\n💡 Si te dio error de conexión, asegúrate de correr esto donde tengas acceso a tu DB o con tu DATABASE_URL seteada.');
  } finally {
    await pool.end();
  }
}

crearNegocioTest();
