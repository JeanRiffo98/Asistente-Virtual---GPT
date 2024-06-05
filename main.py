from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from supabase import create_client
from prettytable import PrettyTable
from openai import OpenAI, AssistantEventHandler
from dotenv import load_dotenv

import json
import os

load_dotenv()
app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

##Utilizamos la variable de entorno API_KEY como valor de api_key
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def get_products(shape, color):
    # Initialize Supabase client
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    supabase = create_client(url, key)
    products_in_room = supabase.from_("bt_products_in_room").select(
        "product_list, image").eq("shape", shape).eq("color_palette", color).limit(1).execute()
    #print("p in room",products_in_room.data)
    product_list = products_in_room.data[0]["product_list"]
    image = products_in_room.data[0]["image"]
    response = supabase.table("product").select("name, sale_price").in_("id", product_list).execute()
    products = response.data
    if products:
        # Crear una tabla con PrettyTable
        table = PrettyTable()
        table.field_names = ["Nombre", "Precio"]

        # Agregar filas a la tabla
        for product in products:
            table.add_row([product['name'], product['sale_price']])

        # Calcular la suma total de los precios
        total_price = sum(product['sale_price'] for product in products)

        # Agregar una fila para la suma total
        table.add_row(["Total", total_price])

        # Convertir la tabla a string
        result_string = table.get_string()
        result_string += f"\nLink a la imagen: {image}"
        #print(result_string)
        return result_string
    else:
        print("No se encontraron productos.")
        return "No se encontraron productos."
    
class CustomEventHandler(AssistantEventHandler):
    
    def __init__(self):
        super().__init__()
        self.first_delta_sent = False

    def on_text_created(self, text) -> None:
        print(f"Omitting on_text_created: {text.value}")
        # Omitiendo la emisión en on_text_created

    def on_text_delta(self, delta, snapshot):
        print(f"Emitting on_text_delta: {delta.value}")
        if not self.first_delta_sent:
            socketio.emit('response', {'text': delta.value, 'isFinal': False, 'isNewMessage': True}, namespace='/chat')
            self.first_delta_sent = True
        else:
            socketio.emit('response', {'text': delta.value, 'isFinal': False, 'isNewMessage': False}, namespace='/chat')

    def on_tool_call_created(self, tool_call):
        print(f"Emitting on_tool_call_created: {tool_call.type}")
        socketio.emit('response', {'text': tool_call.type, 'isFinal': True, 'isNewMessage': True}, namespace='/chat')
        self.first_delta_sent = False

    def on_tool_call_delta(self, delta, snapshot):
        if delta.type == 'code_interpreter':
            if delta.code_interpreter.input:
                print(f"Emitting on_tool_call_delta input: {delta.code_interpreter.input}")
                socketio.emit('response', {'text': delta.code_interpreter.input, 'isFinal': False, 'isNewMessage': False}, namespace='/chat')
            if delta.code_interpreter.outputs:
                print("Emitting on_tool_call_delta outputs")
                for output in delta.code_interpreter.outputs:
                    if output.logs:
                        print(f"Emitting on_tool_call_delta logs: {output.logs}")
                        socketio.emit('response', {'text': output.logs, 'isFinal': True, 'isNewMessage': False}, namespace='/chat')

    def on_event(self, event):
        if event.event == 'thread.run.requires_action':
            run_id = event.data.id
            self.handle_requires_action(event.data, run_id)

    def handle_requires_action(self, data, run_id):
        tool_outputs = []

        for tool in data.required_action.submit_tool_outputs.tool_calls:
            if tool.function.name == "get_products":
                arguments = json.loads(tool.function.arguments)
                tool_outputs.append({
                    "tool_call_id": tool.id,
                    "output": get_products(arguments["shape"], arguments["color"])
                })
        
        self.submit_tool_outputs(tool_outputs, run_id)

    def submit_tool_outputs(self, tool_outputs, run_id):
        with client.beta.threads.runs.submit_tool_outputs_stream(
            thread_id=self.current_run.thread_id,
            run_id=self.current_run.id,
            tool_outputs=tool_outputs,
            event_handler=CustomEventHandler(),
        ) as stream:
            stream.until_done()

@socketio.on('message', namespace='/chat')
def handle_message(data):
    try:
        user_message = data['message']
        print(f"Received message: {user_message}")

        thread = client.beta.threads.create()
        thread_id = thread.id

        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=user_message
        )

        event_handler = CustomEventHandler()  # Crear un nuevo manejador de eventos para cada mensaje

        with client.beta.threads.runs.stream(
            thread_id=thread_id,
            assistant_id=os.environ.get("ASSISTANT_ID"),
            event_handler=event_handler,
        ) as stream:
            stream.until_done()
    except Exception as e:
        print(f"Error: {e}")
        emit('response', {'text': f"Error: {e}", 'isFinal': True, 'isNewMessage': True}, namespace='/chat')
        
@socketio.on('connect', namespace='/chat')
def test_connect():
    print('Client connected')
    # No enviar un mensaje de chat cuando se conecta

@socketio.on('disconnect', namespace='/chat')
def test_disconnect():
    print('Client disconnected')

if __name__ == '__main__':
    socketio.run(app, port=5000)