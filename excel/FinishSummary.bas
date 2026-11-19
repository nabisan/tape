Attribute VB_Name = "FinishSummary"
Option Explicit

Private Const DATA_SHEET As String = "Portfolio Data"
Private Const SUMMARY_SHEET As String = "Summary"
Private Const TITLE_TEXT As String = "Strategy Books  |  Portfolio Summary"
Private Const SUBTITLE_TEXT As String = "Backtest results by strategy, timeframe and settings  -  tape"
Private Const UNITS_TEXT As String = "USD, %"
Private Const FONT_NAME As String = "JetBrains Mono"
Private HEADER_ROW As Long
Private GROUP_ROW As Long
Private NOTE_ROW As Long
Private Const GREY As Long = 9868950
Private Const BAND_FILL As Long = 14277081

Public Sub FinishSummary()
    Dim wb As Workbook: Set wb = ActiveWorkbook
    Dim data As Worksheet, ws As Worksheet, pt As PivotTable
    On Error Resume Next
    Set data = wb.Worksheets(DATA_SHEET)
    Set ws = wb.Worksheets(SUMMARY_SHEET)
    Set pt = ws.PivotTables(1)
    On Error GoTo 0
    If data Is Nothing Or ws Is Nothing Or pt Is Nothing Then
        MsgBox "Needs a '" & DATA_SHEET & "' sheet and a pivot on '" & SUMMARY_SHEET & "'.", vbExclamation
        Exit Sub
    End If

    HEADER_ROW = pt.TableRange1.Row
    If HEADER_ROW < 8 Then
        MsgBox "Move the pivot to start at row 8 or lower so the header fits above it.", vbExclamation
        Exit Sub
    End If
    NOTE_ROW = HEADER_ROW - 1
    GROUP_ROW = HEADER_ROW - 2

    Application.ScreenUpdating = False
    AddStrategyColumn data
    pt.PivotCache.Refresh
    HideField pt, "Link"
    pt.EnableDrilldown = False
    Dim lastCol As Long
    lastCol = pt.TableRange1.Column + pt.TableRange1.Columns.Count - 1
    WriteLinks ws, data, lastCol + 1
    lastCol = lastCol + 1
    ClearAbove ws, lastCol
    WriteHeader ws, data, lastCol
    WriteLegend ws, lastCol
    Dim message As String
    message = AddSlicers(wb, ws, pt, lastCol)
    ws.Activate
    ActiveWindow.DisplayGridlines = False
    ActiveWindow.FreezePanes = False
    ws.Cells(HEADER_ROW + 1, 3).Select
    ActiveWindow.FreezePanes = True
    ws.Range("A1").Select
    Application.ScreenUpdating = True
    MsgBox "Summary finished." & message, vbInformation
End Sub

Private Function ColumnOf(sh As Worksheet, headerRow As Long, title As String) As Long
    Dim found As Range
    Set found = sh.Rows(headerRow).Find(What:=title, LookIn:=xlValues, LookAt:=xlWhole, MatchCase:=True)
    If found Is Nothing Then ColumnOf = 0 Else ColumnOf = found.Column
End Function

Private Sub AddStrategyColumn(data As Worksheet)
    If ColumnOf(data, 1, "Strategy") > 0 Then Exit Sub
    Dim nameCol As Long, tfCol As Long, lastRow As Long, r As Long
    nameCol = ColumnOf(data, 1, "Strategy Name")
    tfCol = ColumnOf(data, 1, "Timeframe")
    data.Columns(nameCol + 1).Insert Shift:=xlToRight
    If tfCol > nameCol Then tfCol = tfCol + 1
    data.Cells(1, nameCol + 1).Value = "Strategy"
    data.Cells(1, nameCol).Copy
    data.Cells(1, nameCol + 1).PasteSpecial Paste:=xlPasteFormats
    Application.CutCopyMode = False
    lastRow = data.Cells(data.Rows.Count, 1).End(xlUp).Row
    For r = 2 To lastRow
        data.Cells(r, nameCol + 1).Value = Family(CStr(data.Cells(r, nameCol).Value), CStr(data.Cells(r, tfCol).Value))
    Next r
    data.Columns(nameCol + 1).ColumnWidth = 16
End Sub

Private Function Family(fullName As String, timeframe As String) As String
    Dim at As Long
    at = InStr(1, fullName, " " & timeframe, vbBinaryCompare)
    If at > 0 Then Family = Trim$(Left$(fullName, at - 1)) Else Family = fullName
End Function

Private Sub HideField(pt As PivotTable, name As String)
    On Error Resume Next
    pt.PivotFields(name).Orientation = xlHidden
    On Error GoTo 0
End Sub

Private Sub WriteLinks(ws As Worksheet, data As Worksheet, col As Long)
    Dim bookCol As Long, linkCol As Long, lastRow As Long, books As String, links As String
    Dim bookAt As Long, r As Long
    bookCol = ColumnOf(data, 1, "Book No")
    bookAt = ColumnOf(ws, HEADER_ROW, "Book No")
    If bookCol = 0 Or bookAt = 0 Then Exit Sub
    lastRow = data.Cells(data.Rows.Count, bookCol).End(xlUp).Row
    linkCol = UrlColumn(data, lastRow)
    If linkCol = 0 Then Exit Sub
    books = "'" & data.Name & "'!" & data.Range(data.Cells(2, bookCol), data.Cells(lastRow, bookCol)).Address
    links = "'" & data.Name & "'!" & data.Range(data.Cells(2, linkCol), data.Cells(lastRow, linkCol)).Address
    ws.Range(ws.Cells(HEADER_ROW, col), ws.Cells(HEADER_ROW + lastRow, col)).Clear
    With ws.Cells(HEADER_ROW, col)
        .Value = "Link"
        .Font.Name = FONT_NAME
        .Font.Bold = True
        .Font.Size = 9
        .Interior.Color = BAND_FILL
    End With
    For r = HEADER_ROW + 1 To HEADER_ROW + lastRow - 1
        ws.Cells(r, col).Formula = "=IFERROR(HYPERLINK(INDEX(" & links & ",MATCH(" & _
            ws.Cells(r, bookAt).Address(False, True) & "," & books & ",0)),""View simulation""),"""")"
    Next r
    With ws.Range(ws.Cells(HEADER_ROW + 1, col), ws.Cells(HEADER_ROW + lastRow - 1, col))
        .Font.Name = FONT_NAME
        .Font.Size = 9
        .Font.Color = RGB(5, 99, 193)
        .Font.Underline = xlUnderlineStyleSingle
    End With
    ws.Columns(col).ColumnWidth = 16
End Sub

Private Function UrlColumn(data As Worksheet, lastRow As Long) As Long
    Dim source As Long, target As Long, r As Long, cell As Range
    source = ColumnOf(data, 1, "Link")
    If source = 0 Then Exit Function
    target = ColumnOf(data, 1, "URL")
    If target = 0 Then target = data.Cells(1, data.Columns.Count).End(xlToLeft).Column + 1
    data.Cells(1, target).Value = "URL"
    For r = 2 To lastRow
        Set cell = data.Cells(r, source)
        If cell.Hyperlinks.Count > 0 Then
            data.Cells(r, target).Value = cell.Hyperlinks(1).Address
        Else
            data.Cells(r, target).Value = cell.Value
        End If
    Next r
    UrlColumn = target
End Function

Private Sub ClearAbove(ws As Worksheet, lastCol As Long)
    With ws.Range(ws.Cells(1, 1), ws.Cells(HEADER_ROW - 1, lastCol))
        .UnMerge
        .Clear
    End With
End Sub

Private Sub WriteHeader(ws As Worksheet, data As Worksheet, lastCol As Long)
    Dim startCol As Long, endCol As Long, lastRow As Long
    startCol = ColumnOf(data, 1, "Start Date")
    endCol = ColumnOf(data, 1, "End Date")
    lastRow = data.Cells(data.Rows.Count, 1).End(xlUp).Row

    With ws.Range(ws.Cells(1, 1), ws.Cells(1, lastCol))
        .Merge
        .Value = TITLE_TEXT
        .HorizontalAlignment = xlCenter
        .Font.Name = FONT_NAME
        .Font.Size = 14
        .Font.Bold = True
    End With
    With ws.Range(ws.Cells(2, 1), ws.Cells(2, lastCol))
        .Merge
        .Value = SUBTITLE_TEXT
        .HorizontalAlignment = xlCenter
        .Font.Name = FONT_NAME
        .Font.Size = 10
        .Font.Bold = True
    End With

    Dim lo As Date, hi As Date
    lo = Application.WorksheetFunction.Min(data.Range(data.Cells(2, startCol), data.Cells(lastRow, startCol)))
    hi = Application.WorksheetFunction.Max(data.Range(data.Cells(2, endCol), data.Cells(lastRow, endCol)))
    With ws.Cells(4, 1)
        .Value = "Period  " & Format$(lo, "yyyy/mm/dd") & " - " & Format$(hi, "yyyy/mm/dd") & _
                 "   |   " & (lastRow - 1) & " books"
        .Font.Name = FONT_NAME
        .Font.Size = 10
    End With
    With ws.Cells(GROUP_ROW - 1, lastCol)
        .Value = UNITS_TEXT
        .HorizontalAlignment = xlRight
        .Font.Name = FONT_NAME
        .Font.Size = 9
        .Font.Color = GREY
    End With
End Sub

Private Function Note(title As String) As String
    Select Case title
        Case "Book No": Note = "Book id, joins the data sheets"
        Case "Strategy Name": Note = "Strategy, bar, settings"
        Case "Timeframe": Note = "Decision bar"
        Case "Start Date": Note = "First session"
        Case "End Date": Note = "Last session"
        Case "Num of Tickers": Note = "Tickers in the book"
        Case "Num of Trades": Note = "Closed round trips"
        Case "Stop-Loss %": Note = "Stop per entry, blank if none"
        Case "Portfolio Capital ($)": Note = "Start value"
        Case "Portfolio Final Value ($)": Note = "End value"
        Case "Portfolio Return %": Note = "End / start - 1"
        Case "Portfolio CAGR %": Note = "Book CAGR"
        Case "Portfolio Volatility %": Note = "Book annualised vol"
        Case "Portfolio Buy & Hold Return %": Note = "Equal-weight hold"
        Case "Avg Capital Used at Cost ($)": Note = "Sum of ticker averages"
        Case "Max Capital Used at Cost ($)": Note = "Peak capital deployed"
        Case "Avg Overnight Capital Required ($)": Note = "Held over the session close"
        Case "Portfolio Max DD % from Initial Capital (Simulation)": Note = "Lowest vs start value"
        Case "Peak to Trough %": Note = "Lowest vs running peak"
        Case "Link": Note = "Simulation on factrades.com"
        Case Else: Note = ""
    End Select
End Function

Private Function Group(title As String) As String
    Select Case title
        Case "Book No", "Strategy Name", "Timeframe": Group = "Book"
        Case "Start Date", "End Date": Group = "Span"
        Case "Num of Tickers", "Num of Trades", "Stop-Loss %": Group = "Activity"
        Case "Portfolio Capital ($)", "Portfolio Final Value ($)": Group = "Capital"
        Case "Portfolio Return %", "Portfolio CAGR %", "Portfolio Volatility %", "Portfolio Buy & Hold Return %": Group = "Returns"
        Case "Avg Capital Used at Cost ($)", "Max Capital Used at Cost ($)", "Avg Overnight Capital Required ($)": Group = "Capital use"
        Case "Portfolio Max DD % from Initial Capital (Simulation)", "Peak to Trough %": Group = "Drawdown"
        Case Else: Group = ""
    End Select
End Function

Private Sub WriteLegend(ws As Worksheet, lastCol As Long)
    Dim c As Long, first As Long, current As String, title As String
    For c = 1 To lastCol
        title = CStr(ws.Cells(HEADER_ROW, c).Value)
        With ws.Cells(NOTE_ROW, c)
            .Value = Note(title)
            .WrapText = True
            .VerticalAlignment = xlTop
            .Font.Name = FONT_NAME
            .Font.Size = 8
            .Font.Color = GREY
        End With
    Next c
    ws.Rows(NOTE_ROW).RowHeight = 24

    first = 1
    current = Group(CStr(ws.Cells(HEADER_ROW, 1).Value))
    For c = 2 To lastCol + 1
        title = ""
        If c <= lastCol Then title = Group(CStr(ws.Cells(HEADER_ROW, c).Value))
        If c > lastCol Or title <> current Then
            DrawBand ws, first, c - 1, current
            first = c
            current = title
        End If
    Next c
    With ws.Range(ws.Cells(GROUP_ROW, 1), ws.Cells(NOTE_ROW, lastCol)).Borders
        .LineStyle = xlContinuous
        .Weight = xlHairline
        .Color = GREY
    End With
End Sub

Private Sub DrawBand(ws As Worksheet, fromCol As Long, toCol As Long, label As String)
    With ws.Range(ws.Cells(GROUP_ROW, fromCol), ws.Cells(GROUP_ROW, toCol))
        If toCol > fromCol Then .Merge
        .Value = label
        .HorizontalAlignment = xlCenter
        .Font.Name = FONT_NAME
        .Font.Size = 9
        .Font.Bold = True
        .Interior.Color = BAND_FILL
    End With
End Sub

Private Function AddSlicers(wb As Workbook, ws As Worksheet, pt As PivotTable, lastCol As Long) As String
    Dim i As Long, sc As SlicerCache, top0 As Double, left0 As Double, height0 As Double
    For i = wb.SlicerCaches.Count To 1 Step -1
        If wb.SlicerCaches(i).PivotTables.Count > 0 Then
            If wb.SlicerCaches(i).PivotTables(1).Name = pt.Name Then wb.SlicerCaches(i).Delete
        End If
    Next i
    top0 = ws.Rows(1).Top + 2
    height0 = ws.Rows(GROUP_ROW - 1).Top - top0 - 2
    left0 = ws.Cells(1, lastCol).Left + ws.Cells(1, lastCol).Width - 240
    On Error Resume Next
    Set sc = wb.SlicerCaches.Add2(pt, "Strategy")
    If sc Is Nothing Then Set sc = wb.SlicerCaches.Add(pt, "Strategy")
    If Not sc Is Nothing Then sc.Slicers.Add ws, , "SummaryStrategy", "Strategy", top0, left0, 240, height0
    On Error GoTo 0
    If sc Is Nothing Then
        AddSlicers = vbCrLf & "Could not add the Strategy slicer. Add it with PivotTable Analyze > Insert Slicer."
    End If
End Function
