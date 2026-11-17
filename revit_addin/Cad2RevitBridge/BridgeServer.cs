using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Threading.Tasks;
using Autodesk.Revit.UI;
using Newtonsoft.Json;

namespace Cad2RevitBridge
{
    /// <summary>
    /// 极简 HTTP 端点：POST /execute 接收一批指令，同步在 Revit UI 线程执行。
    /// 仅监听本机回环地址，不对外网开放。
    /// </summary>
    public class BridgeServer
    {
        private readonly HttpListener _listener = new HttpListener();
        private readonly string _prefix;
        private bool _running;

        public BridgeServer(string prefix)
        {
            _prefix = prefix;
            _listener.Prefixes.Add(prefix);
        }

        public void Start()
        {
            _listener.Start();
            _running = true;
            Task.Run(Loop);
        }

        public void Stop()
        {
            _running = false;
            if (_listener.IsListening) _listener.Stop();
        }

        private async Task Loop()
        {
            while (_running)
            {
                HttpListenerContext context;
                try
                {
                    context = await _listener.GetContextAsync();
                }
                catch (Exception)
                {
                    break;
                }
                Handle(context);
            }
        }

        private void Handle(HttpListenerContext context)
        {
            try
            {
                if (context.Request.HttpMethod != "POST" || context.Request.Url.AbsolutePath != "/execute")
                {
                    Write(context, 404, new { error = "not found" });
                    return;
                }

                using (var reader = new StreamReader(context.Request.InputStream, Encoding.UTF8))
                {
                    var body = reader.ReadToEnd();
                    var payload = JsonConvert.DeserializeObject<ExecuteRequest>(body);
                    var results = ExecuteOnUiThread(payload.Instructions);
                    Write(context, 200, new { results });
                }
            }
            catch (Exception ex)
            {
                Write(context, 500, new { error = ex.Message });
            }
        }

        private IList<ExecutionResult> ExecuteOnUiThread(IList<Instruction> instructions)
        {
            IList<ExecutionResult> results = null;
            var handler = new BridgeExternalEventHandler(instructions, r => results = r);
            var eventInstance = ExternalEvent.Create(handler);
            eventInstance.Raise();
            handler.Wait();
            return results ?? new List<ExecutionResult>();
        }

        private static void Write(HttpListenerContext context, int status, object payload)
        {
            var json = JsonConvert.SerializeObject(payload);
            var bytes = Encoding.UTF8.GetBytes(json);
            context.Response.StatusCode = status;
            context.Response.ContentType = "application/json";
            context.Response.ContentLength64 = bytes.Length;
            context.Response.OutputStream.Write(bytes, 0, bytes.Length);
            context.Response.OutputStream.Close();
        }
    }

    public class ExecuteRequest
    {
        public IList<Instruction> Instructions { get; set; }
    }

    public class BridgeExternalEventHandler : IExternalEventHandler
    {
        private readonly IList<Instruction> _instructions;
        private readonly Action<IList<ExecutionResult>> _callback;
        private readonly System.Threading.ManualResetEvent _done = new System.Threading.ManualResetEvent(false);

        public BridgeExternalEventHandler(IList<Instruction> instructions,
                                          Action<IList<ExecutionResult>> callback)
        {
            _instructions = instructions;
            _callback = callback;
        }

        public void Execute(UIApplication app)
        {
            try
            {
                var executor = new InstructionExecutor(app);
                _callback(executor.ExecuteBatch(_instructions));
            }
            finally
            {
                _done.Set();
            }
        }

        public void Wait() => _done.WaitOne(TimeSpan.FromMinutes(5));

        public string GetName() => "Cad2RevitBridge";
    }
}
