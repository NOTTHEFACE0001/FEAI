"""
Bot de Discord — Carpetas de Asuntos Internos
Un solo archivo: comandos, modal, almacenamiento y arranque del bot.

Requiere: Python 3.10+ (ver requirements.txt para las librerías)
"""

import os
import json
import asyncio
from typing import Optional, Literal
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")  # opcional: si se define, los comandos se sincronizan al instante solo en ese servidor
PORT = int(os.getenv("PORT", 8080))  # Puerto expuesto para servicios de hosting como Render

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
CARPETAS_PATH = os.path.join(DATA_DIR, "carpetas.json")


# ============================================================
#  SERVIDOR HTTP PARA HEALTH CHECK (Render / Hosting)
# ============================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Bot de Discord activo")

    def log_message(self, format, *args):
        # Silenciar logs HTTP repetitivos en la consola
        return

def iniciar_servidor_http(puerto: int):
    server = HTTPServer(("0.0.0.0", puerto), HealthCheckHandler)
    print(f"🌐 Servidor web de health-check iniciado en el puerto {puerto}")
    server.serve_forever()


# ============================================================
#  ALMACENAMIENTO (archivos JSON en data/)
# ============================================================

def asegurar_archivos():
    os.makedirs(DATA_DIR, exist_ok=True)
    for ruta in (CONFIG_PATH, CARPETAS_PATH):
        if not os.path.exists(ruta):
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump({}, f)


def leer_json(ruta):
    asegurar_archivos()
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            contenido = f.read().strip()
            return json.loads(contenido) if contenido else {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error leyendo {ruta}: {e}")
        return {}


def escribir_json(ruta, datos):
    asegurar_archivos()
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)


def config_por_defecto():
    return {
        "roles_permitidos": [],
        "categoria_id": None,
        "archivo_categoria_id": None,
        "log_canal_id": None,
        "contador": 0,
    }


def get_config(guild_id) -> dict:
    todos = leer_json(CONFIG_PATH)
    return todos.get(str(guild_id), config_por_defecto())


def guardar_config(guild_id, config: dict):
    todos = leer_json(CONFIG_PATH)
    todos[str(guild_id)] = config
    escribir_json(CONFIG_PATH, todos)


def siguiente_numero(guild_id) -> str:
    config = get_config(guild_id)
    config["contador"] += 1
    guardar_config(guild_id, config)
    return str(config["contador"]).zfill(4)


def get_carpetas(guild_id) -> dict:
    todos = leer_json(CARPETAS_PATH)
    return todos.get(str(guild_id), {})


def get_carpeta(guild_id, numero: str):
    return get_carpetas(guild_id).get(numero)


def get_carpeta_por_canal(guild_id, canal_id: int):
    for c in get_carpetas(guild_id).values():
        if c.get("canal_id") == canal_id:
            return c
    return None


def guardar_carpeta(guild_id, carpeta: dict):
    todos = leer_json(CARPETAS_PATH)
    todos.setdefault(str(guild_id), {})
    todos[str(guild_id)][carpeta["numero"]] = carpeta
    escribir_json(CARPETAS_PATH, todos)


# ============================================================
#  UTILIDADES: permisos y embed
# ============================================================

def tiene_acceso(interaction: discord.Interaction, config: dict) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    roles_permitidos = config["roles_permitidos"]
    if not roles_permitidos:
        return False
    ids_usuario = {rol.id for rol in interaction.user.roles}
    return any(int(rid) in ids_usuario for rid in roles_permitidos)


def embed_carpeta(carpeta: dict) -> discord.Embed:
    color = 0x1F3A5F if carpeta["estado"] == "abierta" else 0x5F1F1F
    embed = discord.Embed(title=f"📁 Carpeta #{carpeta['numero']}", color=color)
    embed.add_field(name="Motivo", value=carpeta.get("motivo") or "N/A", inline=False)
    embed.add_field(name="Autorizó", value=carpeta.get("autorizado_por") or "N/A", inline=True)
    embed.add_field(name="Agente", value=carpeta.get("agente") or "N/A", inline=True)
    embed.add_field(name="Placa", value=carpeta.get("placa") or "N/A", inline=True)
    embed.add_field(name="Fecha de apertura", value=carpeta.get("fecha") or "N/A", inline=True)
    estado_txt = "🟢 Abierta" if carpeta["estado"] == "abierta" else "🔒 Cerrada"
    embed.add_field(name="Estado", value=estado_txt, inline=True)

    if carpeta["estado"] == "cerrada":
        cerrado_por = carpeta.get("cerrado_por")
        embed.add_field(name="Cerrada por", value=f"<@{cerrado_por}>" if cerrado_por else "N/A", inline=True)
        embed.add_field(name="Motivo de cierre", value=carpeta.get("motivo_cierre") or "Sin especificar", inline=True)

    embed.set_footer(text="Asuntos Internos")
    creado_en = carpeta.get("creado_en")
    if creado_en:
        embed.timestamp = datetime.fromisoformat(creado_en)
    return embed


# ============================================================
#  CLIENTE / BOT
# ============================================================

class CarpetasClient(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.tree.add_command(carpeta_group)
        self.tree.add_command(config_group)

        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"✅ Comandos sincronizados en el servidor de prueba ({GUILD_ID}).")
        else:
            await self.tree.sync()
            print("✅ Comandos sincronizados globalmente (puede tardar hasta 1 hora en aparecer).")


client = CarpetasClient()


@client.event
async def on_ready():
    print(f"✅ Conectado como {client.user}")
    await client.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="carpetas de Asuntos Internos")
    )


# ============================================================
#  MODAL: formulario para abrir una carpeta
# ============================================================

class ModalAbrirCarpeta(discord.ui.Modal):
    def __init__(self, config: dict):
        super().__init__(title="Abrir carpeta - Asuntos Internos")
        self.config = config

        self.motivo = discord.ui.TextInput(style=discord.TextStyle.paragraph, required=True, max_length=1000)
        self.autorizado_por = discord.ui.TextInput(style=discord.TextStyle.short, required=True, max_length=200)
        self.agente = discord.ui.TextInput(style=discord.TextStyle.short, required=True, max_length=200)
        self.placa = discord.ui.TextInput(style=discord.TextStyle.short, required=True, max_length=50)
        fecha_sugerida = datetime.now().strftime("%d/%m/%Y")
        self.fecha = discord.ui.TextInput(
            style=discord.TextStyle.short, required=True, max_length=20, default=fecha_sugerida
        )

        self.add_item(discord.ui.Label(text="Motivo de la carpeta", component=self.motivo))
        self.add_item(discord.ui.Label(text="Autorizó (nombre / cargo)", component=self.autorizado_por))
        self.add_item(discord.ui.Label(text="Agente", component=self.agente))
        self.add_item(discord.ui.Label(text="Placa", component=self.placa))
        self.add_item(discord.ui.Label(text="Fecha de apertura", component=self.fecha))

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        numero = siguiente_numero(guild.id)

        carpeta = {
            "numero": numero,
            "motivo": self.motivo.value,
            "autorizado_por": self.autorizado_por.value,
            "agente": self.agente.value,
            "placa": self.placa.value,
            "fecha": self.fecha.value,
            "estado": "abierta",
            "creado_por": interaction.user.id,
            "creado_en": datetime.now(timezone.utc).isoformat(),
            "canal_id": None,
        }

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        for rol_id in self.config["roles_permitidos"]:
            rol = guild.get_role(int(rol_id))
            if rol:
                overwrites[rol] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )

        categoria = None
        if self.config.get("categoria_id"):
            categoria = guild.get_channel(int(self.config["categoria_id"]))

        try:
            canal = await guild.create_text_channel(
                name=f"carpeta-{numero}",
                category=categoria,
                topic=f"Carpeta #{numero} — {carpeta['motivo']}"[:1024],
                overwrites=overwrites,
            )
            carpeta["canal_id"] = canal.id
            guardar_carpeta(guild.id, carpeta)

            mensaje = await canal.send(content="📁 **Nueva carpeta de Asuntos Internos**", embed=embed_carpeta(carpeta))
            try:
                await mensaje.pin()
            except discord.HTTPException:
                pass

            await interaction.followup.send(f"✅ Carpeta **#{numero}** creada: {canal.mention}", ephemeral=True)

            log_canal_id = self.config.get("log_canal_id")
            if log_canal_id:
                log_canal = guild.get_channel(int(log_canal_id))
                if log_canal:
                    await log_canal.send(embed=embed_carpeta(carpeta))

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ No se pudo crear el canal de la carpeta. Verifica que el bot tenga el permiso "
                "**Gestionar canales** en esa categoría.",
                ephemeral=True,
            )
        except Exception as e:
            print(f"Error creando la carpeta: {e}")
            await interaction.followup.send("❌ Ocurrió un error al crear la carpeta.", ephemeral=True)


# ============================================================
#  COMANDO: /carpeta
# ============================================================

carpeta_group = app_commands.Group(name="carpeta", description="Gestión de carpetas de Asuntos Internos")


@carpeta_group.command(name="abrir", description="Abre una nueva carpeta de investigación")
async def carpeta_abrir(interaction: discord.Interaction):
    config = get_config(interaction.guild_id)

    if not tiene_acceso(interaction, config):
        await interaction.response.send_message(
            "⛔ No tienes un rol autorizado para usar el sistema de carpetas. "
            "Pide a un administrador que te agregue con `/config roles-agregar`.",
            ephemeral=True,
        )
        return

    if not config.get("categoria_id"):
        await interaction.response.send_message(
            "⚠️ Aún no se ha configurado la categoría donde se crean las carpetas. Usa `/config categoria` primero.",
            ephemeral=True,
        )
        return

    await interaction.response.send_modal(ModalAbrirCarpeta(config))


@carpeta_group.command(name="cerrar", description="Cierra la carpeta del canal actual")
@app_commands.describe(motivo="Motivo del cierre")
async def carpeta_cerrar(interaction: discord.Interaction, motivo: Optional[str] = "Sin especificar"):
    config = get_config(interaction.guild_id)
    if not tiene_acceso(interaction, config):
        await interaction.response.send_message("⛔ No tienes un rol autorizado.", ephemeral=True)
        return

    carpeta = get_carpeta_por_canal(interaction.guild_id, interaction.channel_id)
    if not carpeta:
        await interaction.response.send_message(
            "⚠️ Este comando solo se puede usar dentro del canal de una carpeta.", ephemeral=True
        )
        return
    if carpeta["estado"] == "cerrada":
        await interaction.response.send_message("⚠️ Esta carpeta ya está cerrada.", ephemeral=True)
        return

    carpeta["estado"] = "cerrada"
    carpeta["cerrado_por"] = interaction.user.id
    carpeta["cerrado_en"] = datetime.now(timezone.utc).isoformat()
    carpeta["motivo_cierre"] = motivo
    guardar_carpeta(interaction.guild_id, carpeta)

    await interaction.response.send_message(content="🔒 Carpeta cerrada.", embed=embed_carpeta(carpeta))

    canal = interaction.channel
    guild = interaction.guild
    try:
        archivo_id = config.get("archivo_categoria_id")
        if archivo_id:
            categoria = guild.get_channel(int(archivo_id))
            if categoria:
                await canal.edit(category=categoria, sync_permissions=False)
        await canal.set_permissions(guild.default_role, send_messages=False)
        for rol_id in config["roles_permitidos"]:
            rol = guild.get_role(int(rol_id))
            if rol:
                await canal.set_permissions(rol, send_messages=False)
        if not canal.name.startswith("cerrada-"):
            await canal.edit(name=f"cerrada-{carpeta['numero']}")
    except Exception as e:
        print(f"Error al archivar canal: {e}")

    log_canal_id = config.get("log_canal_id")
    if log_canal_id:
        log_canal = guild.get_channel(int(log_canal_id))
        if log_canal:
            await log_canal.send(embed=embed_carpeta(carpeta))


@carpeta_group.command(name="info", description="Muestra la información de una carpeta")
@app_commands.describe(numero="Número de carpeta (ej. 0001)")
async def carpeta_info(interaction: discord.Interaction, numero: Optional[str] = None):
    config = get_config(interaction.guild_id)
    if not tiene_acceso(interaction, config):
        await interaction.response.send_message("⛔ No tienes un rol autorizado.", ephemeral=True)
        return

    if numero:
        carpeta = get_carpeta(interaction.guild_id, numero.zfill(4))
    else:
        carpeta = get_carpeta_por_canal(interaction.guild_id, interaction.channel_id)

    if not carpeta:
        await interaction.response.send_message("❌ No se encontró esa carpeta.", ephemeral=True)
        return

    await interaction.response.send_message(embed=embed_carpeta(carpeta), ephemeral=True)


@carpeta_group.command(name="lista", description="Lista las carpetas del servidor")
@app_commands.describe(estado="Filtrar por estado")
async def carpeta_lista(interaction: discord.Interaction, estado: Optional[Literal["abierta", "cerrada"]] = None):
    config = get_config(interaction.guild_id)
    if not tiene_acceso(interaction, config):
        await interaction.response.send_message("⛔ No tienes un rol autorizado.", ephemeral=True)
        return

    carpetas = list(get_carpetas(interaction.guild_id).values())
    if estado:
        carpetas = [c for c in carpetas if c["estado"] == estado]

    if not carpetas:
        await interaction.response.send_message("No hay carpetas que coincidan.", ephemeral=True)
        return

    carpetas.sort(key=lambda c: c["numero"], reverse=True)
    carpetas = carpetas[:25]

    lineas = [
        f"{'🟢' if c['estado'] == 'abierta' else '🔒'} **#{c['numero']}** — {c['agente']} — <#{c['canal_id']}>"
        for c in carpetas
    ]
    embed = discord.Embed(title="📂 Carpetas de Asuntos Internos", description="\n".join(lineas), color=0x1F3A5F)
    embed.set_footer(text=f"{len(carpetas)} carpeta(s) mostradas")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ============================================================
#  COMANDO: /config  (solo administradores)
# ============================================================

config_group = app_commands.Group(
    name="config",
    description="Configuración del sistema de carpetas (solo administradores)",
    default_permissions=discord.Permissions(administrator=True),
)


@config_group.command(name="roles-agregar", description="Permite a un rol ver y usar las carpetas")
@app_commands.describe(rol="Rol a agregar")
async def config_roles_agregar(interaction: discord.Interaction, rol: discord.Role):
    config = get_config(interaction.guild_id)
    if str(rol.id) in config["roles_permitidos"]:
        await interaction.response.send_message(f"El rol **{rol.name}** ya tenía acceso.", ephemeral=True)
        return
    config["roles_permitidos"].append(str(rol.id))
    guardar_config(interaction.guild_id, config)
    await interaction.response.send_message(
        f"✅ El rol **{rol.name}** ahora puede ver y usar las carpetas.", ephemeral=True
    )


@config_group.command(name="roles-quitar", description="Quita el acceso de un rol a las carpetas")
@app_commands.describe(rol="Rol a quitar")
async def config_roles_quitar(interaction: discord.Interaction, rol: discord.Role):
    config = get_config(interaction.guild_id)
    config["roles_permitidos"] = [r for r in config["roles_permitidos"] if r != str(rol.id)]
    guardar_config(interaction.guild_id, config)
    await interaction.response.send_message(f"✅ El rol **{rol.name}** ya no tiene acceso.", ephemeral=True)


@config_group.command(name="roles-lista", description="Lista los roles con acceso")
async def config_roles_lista(interaction: discord.Interaction):
    config = get_config(interaction.guild_id)
    if not config["roles_permitidos"]:
        await interaction.response.send_message("Todavía no hay roles configurados.", ephemeral=True)
        return
    lista = "\n".join(f"<@&{r}>" for r in config["roles_permitidos"])
    await interaction.response.send_message(f"**Roles con acceso:**\n{lista}", ephemeral=True)


@config_group.command(name="categoria", description="Define la categoría donde se crean las carpetas nuevas")
@app_commands.describe(categoria="Categoría")
async def config_categoria(interaction: discord.Interaction, categoria: discord.CategoryChannel):
    config = get_config(interaction.guild_id)
    config["categoria_id"] = str(categoria.id)
    guardar_config(interaction.guild_id, config)
    await interaction.response.send_message(
        f"✅ Las carpetas nuevas se crearán en **{categoria.name}**.", ephemeral=True
    )


@config_group.command(name="archivo", description="Define la categoría donde se mueven las carpetas cerradas")
@app_commands.describe(categoria="Categoría de archivo")
async def config_archivo(interaction: discord.Interaction, categoria: discord.CategoryChannel):
    config = get_config(interaction.guild_id)
    config["archivo_categoria_id"] = str(categoria.id)
    guardar_config(interaction.guild_id, config)
    await interaction.response.send_message(
        f"✅ Las carpetas cerradas se moverán a **{categoria.name}**.", ephemeral=True
    )


@config_group.command(name="log-canal", description="Canal donde se registran las carpetas abiertas/cerradas")
@app_commands.describe(canal="Canal de texto")
async def config_log_canal(interaction: discord.Interaction, canal: discord.TextChannel):
    config = get_config(interaction.guild_id)
    config["log_canal_id"] = str(canal.id)
    guardar_config(interaction.guild_id, config)
    await interaction.response.send_message(f"✅ Se registrará actividad en {canal.mention}.", ephemeral=True)


@config_group.command(name="ver", description="Muestra la configuración actual")
async def config_ver(interaction: discord.Interaction):
    config = get_config(interaction.guild_id)
    embed = discord.Embed(title="⚙️ Configuración actual", color=0x1F3A5F)
    roles_txt = ", ".join(f"<@&{r}>" for r in config["roles_permitidos"]) if config["roles_permitidos"] else "Ninguno"
    embed.add_field(name="Roles con acceso", value=roles_txt, inline=False)
    embed.add_field(
        name="Categoría de carpetas",
        value=f"<#{config['categoria_id']}>" if config.get("categoria_id") else "No configurada",
        inline=True,
    )
    embed.add_field(
        name="Categoría de archivo",
        value=f"<#{config['archivo_categoria_id']}>" if config.get("archivo_categoria_id") else "No configurada",
        inline=True,
    )
    embed.add_field(
        name="Canal de registro",
        value=f"<#{config['log_canal_id']}>" if config.get("log_canal_id") else "No configurado",
        inline=True,
    )
    embed.add_field(name="Carpetas creadas", value=str(config["contador"]), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ============================================================
#  ARRANQUE
# ============================================================

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ Falta DISCORD_TOKEN en el archivo .env")
    
    # Iniciar servidor HTTP en un hilo secundario para cumplir con el Health Check de Render
    threading.Thread(target=iniciar_servidor_http, args=(PORT,), daemon=True).start()
    
    client.run(TOKEN)
