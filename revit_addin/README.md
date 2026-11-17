# Revit 参考 Add-in

把 `run_revit_script` 的批次指令落到真实 Revit 里执行。**这是参考实现，不是成品插件**：需要按你的 Revit 版本、族库命名和项目模板调整。

## 组成

| 文件 | 作用 |
| --- | --- |
| `App.cs` | `IExternalApplication`，Revit 启动时拉起 HTTP 桥接，保存 UIApplication 上下文 |
| `BridgeServer.cs` | `HttpListener`，监听 `http://127.0.0.1:8787/execute` |
| `InstructionExecutor.cs` | 批次事务、四类构件创建、失败整批回滚 |
| `Instruction.cs` | 与 `generate_revit_script` 输出一致的 DTO |

## 编译

1. Visual Studio 打开 `Cad2RevitBridge.csproj`；
2. 修改 `HintPath` 指向本机 `RevitAPI.dll` / `RevitAPIUI.dll`（如 `Revit 2022`）；
3. `Newtonsoft.Json` 通过 NuGet 安装；
4. 输出 `Cad2RevitBridge.dll` 复制到 `%APPDATA%\Autodesk\Revit\Addins\<版本>\`；
5. 同目录放 `Cad2RevitBridge.addin`：

```xml
<?xml version="1.0" encoding="utf-8"?>
<RevitAddIns>
  <AddIn Type="Application">
    <Name>Cad2RevitBridge</Name>
    <Assembly>Cad2RevitBridge.dll</Assembly>
    <FullClassName>Cad2RevitBridge.App</FullClassName>
    <VendorId>cad2revit</VendorId>
    <VendorDescription>CAD to Revit MCP bridge</VendorDescription>
  </AddIn>
</RevitAddIns>
```

## 与 MCP 服务对接

在 `config.json` 里设置：

```json
{"bridge_url": "http://127.0.0.1:8787"}
```

`run_revit_script` 会自动切到 `bridge` 模式，POST 到 `/execute`，读取返回的 `results`。

## 必须自行确认的点

- **族的命名**：`family_map` 里的 family/type 必须与项目里已载入的族完全一致，否则返回 `E-3001`；
- **标高**：`level_map` 的名称必须与 Revit 里的 Level 名称一致；
- **梁的定位**：参考实现按梁线直接放置，未做支座与标高偏移修正；
- **墙厚**：`Wall.Width` 赋值在部分族类型上会被类型参数覆盖，需要按项目规则调整；
- **单位**：Revit API 内部为英尺，桥接层已做 `mm → feet` 换算。

## 安全

- 只监听 `127.0.0.1`，不要改成 `0.0.0.0`；
- 不提供删除构件的能力；
- 每批固定一个 Transaction，失败即回滚。
