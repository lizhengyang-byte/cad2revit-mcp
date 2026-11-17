using Autodesk.Revit.UI;

namespace Cad2RevitBridge
{
    /// <summary>
    /// 注册 ExternalApplication，在 Revit 启动时拉起本地 HTTP 桥接。
    /// 参考实现，不包含 UI 面板与业务命令。
    /// </summary>
    public class App : IExternalApplication
    {
        private BridgeServer _server;

        public Result OnStartup(UIControlledApplication application)
        {
            RevitContext.Current = application;
            _server = new BridgeServer("http://127.0.0.1:8787/");
            _server.Start();
            return Result.Succeeded;
        }

        public Result OnShutdown(UIControlledApplication application)
        {
            _server?.Stop();
            return Result.Succeeded;
        }
    }

    /// <summary>保存 UIControlledApplication，供 HTTP 线程通过 ExternalEvent 回到 UI 线程。</summary>
    public static class RevitContext
    {
        public static UIControlledApplication Current { get; set; }
    }
}
